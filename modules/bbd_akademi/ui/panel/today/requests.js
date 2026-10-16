// BBD Akademi — Talepler (F-A14): izin (F-P10), görüşme (F-P11), etüt (F-S13).
//
// Üç kuyruk tek ekranda. Onay/ret gerekçesi veliye/öğrenciye bildirimle gider
// ve Akademi denetim kaydına yazılır. İzin onaylanınca yoklamada "izinli"
// kendiliğinden işaretlenir (Akademi kuralı) — NE kadarının işaretlendiği türe
// bağlıdır (tam gün / saat aralığı / erken çıkış / mazeret; V4): listede tür
// rozeti, mazerette etkilenen dersler ("09:20 Matematik"), onay penceresinde
// türe göre doğru etki metni (`attendance-manage.js` → `leaveApproveText`).
//
// `etutScheduleDialog` Ders programı bölümünün etüt kuyruğunda da kullanılır.

import { h, loadStyles } from '../../../ui-kit/kit.js';
import { badge, tabBar } from '../../../ui-kit/layout.js';
import {
  DAY_LABEL, actionButton, fmtDate, fmtDateTime, formDialog, pagedList, personName,
  reasonDialog, runAction, sectionHead, selectBox, statusBadge, todayIst, toolbar,
} from '../common.js';
import { LEAVE_REASONS, leaveApproveText, leaveKindDetail, leaveLessons } from '../attendance-manage.js';
import { templateField } from '../shared/text-templates.js';

const CHANNEL = { phone: 'telefonla', in_person: 'yüz yüze' };
const STATUS = { requested: ['Bekliyor', 'warn'], approved: ['Onaylandı', 'good'], scheduled: ['Zaman verildi', 'good'],
  declined: ['Reddedildi', 'dim'], cancelled: ['İptal', 'dim'], done: ['Tamamlandı', 'good'] };
const MODES = { in_person: 'Yüz yüze', phone: 'Telefon', online: 'Çevrim içi' };

const studentCell = (row) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', row.student?.displayName || '—'), h('small', '', row.student?.classGroup?.name || ''));
  return cell;
};

export function section(app) {
  loadStyles(import.meta.url, 'requests.css');
  const node = h('div', 'ak-stack');
  node.append(sectionHead('Talepler', { subtitle: 'Velilerden ve öğrencilerden gelen izin, görüşme ve etüt talepleri.' }));
  const tabs = tabBar([
    { key: 'leave', label: 'İzin' },
    { key: 'meeting', label: 'Görüşme' },
    { key: 'etut', label: 'Etüt' },
  ], 'leave', (key) => show(key));
  const slot = h('div');
  node.append(tabs.node, slot);
  const bodies = {};
  const builders = { leave: () => leaveView(app, tabs), meeting: () => meetingView(app, tabs),
    etut: () => etutView(app, tabs) };

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      if (!started) { started = true; show('leave'); }
      if (payload?.tab && builders[payload.tab]) tabs.select(payload.tab);
    },
    destroy() { for (const body of Object.values(bodies)) body.destroy?.(); },
  };
}

function statusFilter(onChange, initial = 'requested') {
  return selectBox([
    { value: 'requested', label: 'Bekleyenler' }, { value: 'approved', label: 'Onaylananlar' },
    { value: 'scheduled', label: 'Zaman verilenler' }, { value: 'declined', label: 'Reddedilenler' },
    { value: 'done', label: 'Tamamlananlar' }, { value: '', label: 'Tümü' },
  ], initial, onChange, { label: 'Durum' });
}

// ---------------------------------------------------------------------- izin

function leaveView(app, tabs) {
  const node = h('div', 'ak-stack');
  const status = statusFilter(() => list.reload());
  node.append(toolbar(status.node));
  // Öğrencinin o günkü yoklaması (mazeret / saat aralığı dersleri) — yalnız
  // istenince ve öğrenci+gün başına bir kez.
  const days = new Map();
  const dayOf = (row) => {
    const key = `${row.student?.id}|${row.from}`;
    if (!days.has(key)) {
      days.set(key, app.api.get(`/v1/students/${row.student.id}/attendance/days/${row.from}`)
        .catch((failure) => { days.delete(key); throw failure; }));
    }
    return days.get(key);
  };
  const list = pagedList({
    app,
    path: '/v1/admin/leave-requests',
    query: () => ({ status: status.value || undefined }),
    onLoaded: pendingBadge(tabs, 'leave', status),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: studentCell },
      { key: 'kind', label: 'Tür', width: 'minmax(150px, 1.1fr)', cell: (row) => kindCell(row) },
      { key: 'range', label: 'Tarih', width: '170px', cell: (row) => (row.from === row.to ? fmtDate(row.from)
        : `${fmtDate(row.from)} – ${fmtDate(row.to)}`) },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', LEAVE_REASONS[row.reason] || row.reason), h('small', '', row.description || ''));
        return cell;
      } },
      { key: 'by', label: 'Bildiren', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', personName(row.createdBy)),
          h('small', '', [fmtDateTime(row.createdAt), CHANNEL[row.channel]].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(STATUS, row.status) },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.status !== 'requested') {
          if (row.decisionNote) actions.append(h('small', 'ak-muted', row.decisionNote));
          return actions;
        }
        actions.append(
          actionButton(app, 'admin_approveLeaveRequest', 'Onayla', { variant: 'primary', onClick: () => approve(row) }),
          actionButton(app, 'admin_declineLeaveRequest', 'Reddet', { variant: 'ghost', onClick: () => decline(row) }),
        );
        return actions;
      } },
    ],
    empty: { title: 'Bu süzgeçte izin talebi yok', text: 'Veliler uygulamadan izin ya da mazeret bildirdiğinde burada görünür.' },
  });
  node.append(list.node);

  /** Tür rozeti + ayrıntı; mazerette "Dersler" düğmesi etkilenen dersleri yazar. */
  function kindCell(row) {
    const info = leaveKindDetail(row);
    const cell = h('div', 'ak-cell-stack ak-req-kind');
    cell.append(badge(info.label, info.tone));
    if (info.detail) cell.append(h('small', '', info.detail));
    const canResolve = (info.kind === 'retro_excuse' || info.kind === 'partial') && row.student?.id
      && (row.affectedSessionIds || []).length && app.can('students_getAttendanceDay');
    if (canResolve) {
      const more = h('button', 'ak-link ak-req-lessons', 'Dersleri göster');
      more.type = 'button';
      more.addEventListener('click', async (event) => {
        event.stopPropagation();
        more.disabled = true;
        more.textContent = 'Yükleniyor…';
        try {
          const lessons = leaveLessons(row, await dayOf(row));
          const text = lessons.length ? lessons.map((item) => item.text).join(', ') : 'Ders bulunamadı';
          more.replaceWith(h('small', 'ak-req-lesson-list', text));
        } catch {
          more.disabled = false;
          more.textContent = 'Dersler yüklenemedi · yeniden dene';
        }
      });
      cell.append(more);
    }
    return cell;
  }

  async function approve(row) {
    let lessons = null;
    if (row.kind === 'retro_excuse' && row.student?.id && app.can('students_getAttendanceDay')) {
      lessons = await dayOf(row).then((day) => leaveLessons(row, day)).catch(() => null);
    }
    const note = await reasonDialog(app, { title: `${row.student?.displayName}: ${row.kind === 'retro_excuse'
      ? 'mazereti onayla' : 'izni onayla'}`,
    description: leaveApproveText(row, lessons),
    confirmLabel: 'Onayla', minLength: 0, danger: false });
    if (note === null) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/leave-requests/${row.id}/approve`,
      { note: note || null }, { key: `leave-ok-${row.id}` }), { success: row.kind === 'retro_excuse'
      ? 'Mazeret onaylandı.' : 'İzin onaylandı.' });
    if (next) list.replace((item) => item.id === row.id, next);
  }

  async function decline(row) {
    const reason = await reasonDialog(app, { title: `${row.student?.displayName}: ${row.kind === 'retro_excuse'
      ? 'mazereti reddet' : 'izni reddet'}`,
    description: 'Gerekçe veliye bildirilir.', confirmLabel: 'Reddet' });
    if (!reason) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/leave-requests/${row.id}/decline`,
      { reason }, { key: `leave-no-${row.id}` }), { success: 'Talep reddedildi.' });
    if (next) list.replace((item) => item.id === row.id, next);
  }

  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

/** Sekme rozeti: yüklenen sayfalardaki bekleyen talep sayısı (ek istek yok). */
function pendingBadge(tabs, key, status) {
  return (rows) => {
    if (status.value === 'requested') tabs.badge(key, rows.filter((row) => row.status === 'requested').length);
  };
}

// ------------------------------------------------------------------- görüşme

function meetingView(app, tabs) {
  const node = h('div', 'ak-stack');
  const status = statusFilter(() => list.reload());
  node.append(toolbar(status.node));
  const list = pagedList({
    app,
    path: '/v1/admin/meeting-requests',
    query: () => ({ status: status.value || undefined }),
    onLoaded: pendingBadge(tabs, 'meeting', status),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: studentCell },
      { key: 'topic', label: 'Konu', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.topic), h('small', '', [MODES[row.mode], row.note].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'slots', label: 'Tercih / randevu', width: 'minmax(0, 1.6fr)', cell: (row) => {
        if (row.scheduled?.startsAt) {
          return `${fmtDateTime(row.scheduled.startsAt)}${row.scheduled.location ? ` · ${row.scheduled.location}` : ''}`;
        }
        return (row.preferredSlots || []).map((slot) => `${fmtDate(slot.date)} ${slot.from}–${slot.to}`).join(' · ') || '—';
      } },
      { key: 'by', label: 'İsteyen', cell: (row) => personName(row.createdBy) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(STATUS, row.status) },
      { key: 'actions', label: '', width: '210px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.status === 'requested' || row.status === 'scheduled') {
          actions.append(actionButton(app, 'admin_scheduleMeeting', row.status === 'scheduled' ? 'Saati değiştir' : 'Zaman ver',
            { variant: row.status === 'requested' ? 'primary' : '', onClick: () => schedule(row) }));
        }
        if (row.status === 'scheduled') {
          actions.append(actionButton(app, 'admin_completeMeeting', 'Yapıldı', { variant: 'ghost', onClick: () => complete(row) }));
        }
        if (row.status === 'requested') {
          actions.append(actionButton(app, 'admin_declineMeeting', 'Reddet', { variant: 'ghost', onClick: () => decline(row) }));
        }
        return actions;
      } },
    ],
    empty: { title: 'Bekleyen görüşme talebi yok', text: 'Veli görüşme istediğinde burada görünür.' },
  });
  node.append(list.node);

  async function schedule(row) {
    const first = row.preferredSlots?.[0];
    const staff = await app.lookups.options('staff', { empty: 'Belirtilmedi' }).catch(() => [{ value: '', label: 'Belirtilmedi' }]);
    const next = await formDialog(app, {
      title: `${row.student?.displayName}: görüşme zamanı`,
      description: `Konu: ${row.topic}. ${(row.preferredSlots || []).length ? 'Velinin tercihleri: '
        + row.preferredSlots.map((slot) => `${fmtDate(slot.date)} ${slot.from}–${slot.to}`).join(', ') : ''}`,
      value: { day: first?.date || todayIst(1), from: first?.from || '10:00', to: first?.to || '10:30',
        staffId: '', location: row.mode === 'in_person' ? '' : MODES[row.mode] },
      fields: [
        { key: 'day', label: 'Gün', type: 'date', required: true },
        { key: 'from', label: 'Başlangıç (SS:DD)', required: true, maxLength: 5, validate: clockCheck },
        { key: 'to', label: 'Bitiş (SS:DD)', required: true, maxLength: 5, validate: clockCheck },
        { key: 'staffId', label: 'Görüşecek personel', type: 'select', options: staff },
        { key: 'location', label: 'Yer', maxLength: 80 },
        { key: 'note', label: 'Veliye not', maxLength: 300, wide: true },
      ],
      submitLabel: 'Zamanı bildir',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/meeting-requests/${row.id}/schedule`, {
        startsAt: `${draft.day}T${draft.from}:00+03:00`, endsAt: `${draft.day}T${draft.to}:00+03:00`,
        location: draft.location || null, staffId: draft.staffId || null, note: draft.note || null }, { key }),
    });
    if (next) { app.toast('Görüşme zamanı veliye bildirildi.', 'good'); list.replace((item) => item.id === row.id, next); }
  }

  async function complete(row) {
    // Akademi README §14 madde 115: veli görüşmesi notu şablondan (öğrenci, veli, tarih doldurulur).
    const next = await formDialog(app, {
      title: 'Görüşme yapıldı',
      description: 'İsteğe bağlı kısa not (en çok 300 karakter).',
      wide: true,
      fields: [
        templateField(app, { kind: 'parent_meeting', target: 'note', maxLength: 300, inlineActions: true,
          context: () => ({ studentId: row.student?.id }) }),
        { key: 'note', label: 'Görüşme notu', type: 'textarea', maxLength: 300, wide: true },
      ],
      value: { note: '' },
      submitLabel: 'Kaydet',
      onSubmit: (draft) => app.api.post(`/v1/admin/meeting-requests/${row.id}/complete`,
        { note: String(draft.note || '').trim() || null }, { key: `meet-done-${row.id}` }),
    });
    if (!next) return;
    app.toast('Görüşme tamamlandı.', 'good');
    list.replace((item) => item.id === row.id, next);
  }

  async function decline(row) {
    const reason = await reasonDialog(app, { title: 'Görüşme talebini reddet', description: 'Gerekçe veliye bildirilir.',
      confirmLabel: 'Reddet' });
    if (!reason) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/meeting-requests/${row.id}/decline`,
      { reason }, { key: `meet-no-${row.id}` }), { success: 'Talep reddedildi.' });
    if (next) list.replace((item) => item.id === row.id, next);
  }

  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

function clockCheck(raw) {
  return raw && !/^([01]\d|2[0-3]):[0-5]\d$/.test(raw) ? 'Saati 09:30 gibi yazın.' : null;
}

// ---------------------------------------------------------------------- etüt

function etutView(app, tabs) {
  const node = h('div', 'ak-stack');
  const status = statusFilter(() => list.reload());
  node.append(toolbar(status.node));
  const list = pagedList({
    app,
    path: '/v1/admin/etut-requests',
    query: () => ({ status: status.value || undefined }),
    onLoaded: pendingBadge(tabs, 'etut', status),
    columns: etutColumns(app, {
      onSchedule: (row) => etutScheduleDialog(app, row).then((next) => next && list.replace((item) => item.id === row.id, next)),
      onDecline: (row) => etutDecline(app, row).then((next) => next && list.replace((item) => item.id === row.id, next)),
    }),
    empty: { title: 'Bekleyen etüt talebi yok', text: 'Öğrenci etüt istediğinde burada görünür.' },
  });
  node.append(list.node);
  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

/** Etüt talebi sütunları — Ders programı bölümü de kullanır. */
export function etutColumns(app, { onSchedule, onDecline }) {
  return [
    { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: studentCell },
    { key: 'subject', label: 'Ders / konu', width: 'minmax(0, 1.6fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', row.subject?.name || '—'), h('small', '', row.topic?.name || ''));
      return cell;
    } },
    { key: 'days', label: 'Tercih günleri', cell: (row) => (row.preferredDays || []).map((day) => DAY_LABEL[day] || day).join(', ') || '—' },
    { key: 'note', label: 'Not', width: 'minmax(0, 1.2fr)', cell: (row) => row.note || '—' },
    { key: 'status', label: 'Durum', cell: (row) => (row.status === 'scheduled' && row.scheduledStartsAt
      ? badge(`Zaman verildi · ${fmtDateTime(row.scheduledStartsAt)}`, 'good') : statusBadge(STATUS, row.status)) },
    { key: 'actions', label: '', width: '170px', cell: (row) => {
      const actions = h('div', 'ak-row-actions');
      if (row.status !== 'requested') return actions;
      actions.append(
        actionButton(app, 'admin_scheduleEtut', 'Zaman ver', { variant: 'primary', onClick: () => onSchedule(row) }),
        actionButton(app, 'admin_declineEtut', 'Reddet', { variant: 'ghost', onClick: () => onDecline(row) }),
      );
      return actions;
    } },
  ];
}

/**
 * Etüt talebine zaman ver: yeni etüt dersi açar (`event`) ya da var olan bir
 * etüde ekler (`lessonEventId`). Öğrenciye/veliye bildirim gider.
 */
export async function etutScheduleDialog(app, request) {
  const teachers = await app.lookups.options('teachers', { empty: 'Belirtilmedi' })
    .catch(() => [{ value: '', label: 'Belirtilmedi' }]);
  return formDialog(app, {
    title: `${request.student?.displayName}: etüt zamanı`,
    description: `${request.subject?.name || ''}${request.topic?.name ? ` · ${request.topic.name}` : ''}. `
      + `Tercih: ${(request.preferredDays || []).map((day) => DAY_LABEL[day] || day).join(', ') || 'yok'}.`,
    value: { mode: 'new', day: todayIst(1), from: '15:00', to: '15:40', teacherId: '', notify: true },
    fields: [
      { key: 'mode', label: 'Nasıl', type: 'select', options: [
        { value: 'new', label: 'Yeni etüt dersi aç' }, { value: 'existing', label: 'Var olan etüde ekle' }] },
      { key: 'lessonEventId', label: 'Var olan etüdün kimliği (yalnız "ekle"de)', maxLength: 60,
        hint: 'Ders programı → Etütler listesinden kopyalanır.' },
      { key: 'day', label: 'Gün', type: 'date' },
      { key: 'from', label: 'Başlangıç (SS:DD)', maxLength: 5, validate: clockCheck },
      { key: 'to', label: 'Bitiş (SS:DD)', maxLength: 5, validate: clockCheck },
      { key: 'teacherId', label: 'Öğretmen', type: 'select', options: teachers },
      { key: 'room', label: 'Derslik', maxLength: 40 },
      { key: 'note', label: 'Not', maxLength: 300, wide: true },
      { key: 'notify', label: 'Öğrenciye ve veliye bildir', type: 'checkbox' },
    ],
    submitLabel: 'Zamanı ver',
    onSubmit: (draft, { key }) => {
      if (draft.mode === 'existing') {
        return app.api.post(`/v1/admin/etut-requests/${request.id}/schedule`,
          { lessonEventId: String(draft.lessonEventId || '').trim(), event: null }, { key });
      }
      return app.api.post(`/v1/admin/etut-requests/${request.id}/schedule`, {
        lessonEventId: null,
        event: {
          kind: 'etut',
          title: null,
          subjectId: request.subject?.id || null,
          topicId: request.topic?.id || null,
          teacherId: draft.teacherId || null,
          room: draft.room || null,
          startsAt: `${draft.day}T${draft.from}:00+03:00`,
          endsAt: `${draft.day}T${draft.to}:00+03:00`,
          studentIds: [request.student.id],
          classGroupIds: [],
          etutRequestIds: [request.id],
          note: draft.note || null,
          notify: Boolean(draft.notify),
        },
      }, { key });
    },
  });
}

export async function etutDecline(app, request) {
  const reason = await reasonDialog(app, { title: `${request.student?.displayName}: etüt talebini reddet`,
    description: 'Gerekçe öğrenciye bildirilir.', confirmLabel: 'Reddet' });
  if (!reason) return null;
  return runAction(app, () => app.api.post(`/v1/admin/etut-requests/${request.id}/decline`,
    { reason }, { key: `etut-no-${request.id}` }), { success: 'Etüt talebi reddedildi.' });
}
