// BBD Akademi — Koçluk → Görüşmeler (F-K17; bayrak `coach_meetings`).
//
// Kurumun bütün koç görüşmeleri (`admin_listCoachMeetings`; tarih, koç,
// öğrenci, durum süzgeci). Ayrıntı (`admin_getCoachMeeting`): katılımcılar,
// biçim / oda / bağlantı, "geçen sefer kararlaştırılanlar"; karar
// (`admin_decideCoachMeeting`: onayla · reddet · ertele · iptal · yapıldı ·
// gelinmedi — talep edene bildirilir), görüşme notu + eylem maddeleri
// (`admin_putCoachMeetingNote`; not bir koç notudur, görünürlük seçilir) ve
// eylem maddesini plan görevine ya da hedef önerisine dönüştürme
// (`admin_convertMeetingActionItem`). "Görüşme planla" öğrenci seçtirir
// (`admin_createCoachMeeting`).

import { button, h } from '../../ui-kit/kit.js';
import { badge, skeletonRows } from '../../ui-kit/layout.js';
import {
  actionButton, clockInput, dateInput, errorBox, fmtDateTime, formDialog, istanbulStamp, kv, openDrawer,
  optionsOf, pagedList, selectBox, statusBadge, studentPicker, todayIst, toolbar,
} from './common.js';
import {
  GOAL_EXAM_TYPES, GOAL_KINDS, MEETING_DECISIONS, MEETING_MODES, MEETING_PARTICIPANTS, MEETING_STATUS,
  NOTE_VISIBILITY, REQUESTED_BY, TASK_TYPES, goalBody, meetingDecisionBody,
} from './coaching-logic.js';
import { coachOptions, studentCell } from './coaching-shared.js';
import { meetingDialog } from './coaching-students.js';

export function tab(app) {
  const node = h('div', 'ak-stack');
  const filter = { from: todayIst(-7), to: todayIst(30), coachId: '', status: '', studentId: '' };
  const from = dateInput(filter.from, (value) => { filter.from = value || ''; list.reload(); }, 'Başlangıç');
  const to = dateInput(filter.to, (value) => { filter.to = value || ''; list.reload(); }, 'Bitiş');
  const status = selectBox(optionsOf(Object.fromEntries(Object.entries(MEETING_STATUS).map(([key, [label]]) => [key, label])),
    { empty: 'Bütün durumlar' }), '', (value) => { filter.status = value; list.reload(); }, { label: 'Durum' });
  const coachSlot = h('div');
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachSlot.append(selectBox(options, '', (value) => { filter.coachId = value; list.reload(); }, { label: 'Koç' }).node);
  });
  const create = actionButton(app, 'admin_createCoachMeeting', 'Görüşme planla', { variant: 'primary',
    onClick: () => pickStudent(app, async (student) => { if (await meetingDialog(app, student)) list.reload(); }) });
  const studentNote = h('span', 'ak-muted');
  const clear = button('Öğrenci süzgecini kaldır', { variant: 'ghost', onClick: () => {
    filter.studentId = ''; studentNote.textContent = ''; clear.hidden = true; list.reload();
  } });
  clear.hidden = true;
  node.append(toolbar(from.node, to.node, status.node, coachSlot, studentNote, clear, create));

  const list = pagedList({
    app,
    path: '/v1/admin/coaching/meetings',
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    onRow: (row) => openMeeting(app, row, (next) => list.replace((item) => item.id === next.id, next)),
    columns: [
      { key: 'startsAt', label: 'Zaman', width: '150px', cell: (row) => fmtDateTime(row.startsAt) },
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.2fr)', cell: (row) => studentCell(row.student) },
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1fr)', cell: (row) => row.coach?.displayName || '—' },
      { key: 'mode', label: 'Biçim', width: '150px', cell: (row) => `${MEETING_MODES[row.mode] || row.mode} · ${MEETING_PARTICIPANTS[row.participants] || ''}` },
      { key: 'topic', label: 'Konu', width: 'minmax(0, 1.4fr)', cell: (row) => row.topic || '—' },
      { key: 'by', label: 'İsteyen', width: '100px', cell: (row) => REQUESTED_BY[row.requestedByRole] || '—' },
      { key: 'status', label: 'Durum', width: '120px', cell: (row) => statusBadge(MEETING_STATUS, row.status) },
    ],
    empty: { title: 'Bu aralıkta görüşme yok', text: 'Öğrenci, veli ya da koç görüşme istediğinde burada görünür.' },
  });
  node.append(list.node);
  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.studentId) {
        filter.studentId = payload.studentId;
        studentNote.textContent = 'Tek öğrencinin görüşmeleri';
        clear.hidden = false;
        started = true;
        list.reload();
        return;
      }
      if (!started) { started = true; list.reload(); }
    },
    destroy() { list.destroy(); from.destroy?.(); to.destroy?.(); },
  };
}

/** Öğrenci seçtirir (çekmece içinde arama). */
export function pickStudent(app, onPick) {
  const view = openDrawer(app, { title: 'Öğrenci seçin', subtitle: 'Ad ya da okul numarasıyla arayın' });
  const picker = studentPicker(app, { onPick: (student) => { view.close(); picker.destroy?.(); onPick(student); } });
  view.body.replaceChildren(picker.node);
  picker.focus?.();
}

/** Görüşme ayrıntısı. `onChange(meeting)`. */
export async function openMeeting(app, row, onChange) {
  const view = openDrawer(app, { title: row.student?.displayName || 'Görüşme', subtitle: fmtDateTime(row.startsAt) });
  view.body.replaceChildren(skeletonRows(6, 2));
  let meeting = row;

  const render = () => {
    const parts = [kv([
      ['Öğrenci', meeting.student?.displayName || '—'],
      ['Koç', meeting.coach?.displayName || '—'],
      ['Zaman', `${fmtDateTime(meeting.startsAt)} – ${fmtDateTime(meeting.endsAt).slice(-5)}`],
      ['Biçim', [MEETING_MODES[meeting.mode], meeting.room, meeting.onlineUrl].filter(Boolean).join(' · ') || '—'],
      ['Katılımcı', MEETING_PARTICIPANTS[meeting.participants] || '—'],
      ['İsteyen', `${meeting.requestedBy?.displayName || '—'} (${REQUESTED_BY[meeting.requestedByRole] || ''})`],
      ['Konu', meeting.topic || '—'],
      ['Durum', statusBadge(MEETING_STATUS, meeting.status)],
      meeting.declineReason ? ['Gerekçe', meeting.declineReason] : null,
      meeting.earlyWarningId ? ['Kaynak', 'Erken uyarıdan açıldı'] : null,
    ])];
    const decisions = h('div', 'ak-row-actions');
    for (const [decision, [label, states]] of Object.entries(MEETING_DECISIONS)) {
      if (!states.includes(meeting.status)) continue;
      decisions.append(actionButton(app, 'admin_decideCoachMeeting', label,
        { variant: decision === 'confirm' || decision === 'done' ? 'primary' : 'ghost', onClick: () => decide(decision) }));
    }
    if (decisions.childElementCount) parts.push(decisions);
    if (meeting.previousActionItems?.length) {
      parts.push(h('h4', 'ak-coach-sub', 'Geçen sefer kararlaştırılanlar'));
      parts.push(actionList(meeting.previousActionItems, false));
    }
    parts.push(h('h4', 'ak-coach-sub', 'Görüşme notu'));
    if (meeting.note) {
      parts.push(h('p', 'ak-coach-text', meeting.note.text),
        h('small', 'ak-muted', `${NOTE_VISIBILITY[meeting.note.visibility] || ''}${meeting.note.updatedAt ? ` · ${fmtDateTime(meeting.note.updatedAt)}` : ''}`));
      if (meeting.note.actionItems?.length) {
        parts.push(h('h4', 'ak-coach-sub', 'Eylem maddeleri'), actionList(meeting.note.actionItems, true));
      }
    } else {
      parts.push(h('p', 'ak-muted', 'Not yazılmadı.'));
    }
    parts.push(toolbar(actionButton(app, 'admin_putCoachMeetingNote', meeting.note ? 'Notu düzenle' : 'Not yaz',
      { variant: 'ghost', onClick: () => editNote() })));
    view.body.replaceChildren(...parts);
  };

  const actionList = (items, convertible) => {
    const box = h('div', 'ak-stack');
    for (const item of items) {
      const line = h('div', 'ak-coach-action');
      line.append(h('span', '', `${item.done ? '✓ ' : ''}${item.text}`));
      if (item.convertedTo) {
        line.append(badge(item.convertedTo.type === 'plan_task' ? 'Göreve dönüştü' : 'Hedef önerisi oldu', 'good'));
      } else if (convertible) {
        line.append(actionButton(app, 'admin_convertMeetingActionItem', 'Göreve dönüştür', { variant: 'ghost',
          onClick: () => convert(item, 'plan_task') }),
        actionButton(app, 'admin_convertMeetingActionItem', 'Hedef önerisi yap', { variant: 'ghost',
          onClick: () => convert(item, 'goal_proposal') }));
      }
      box.append(line);
    }
    return box;
  };

  async function decide(decision) {
    const needsTime = decision === 'reschedule';
    const result = await formDialog(app, {
      title: MEETING_DECISIONS[decision][0],
      description: 'Karar talep edene bildirilir.',
      fields: [
        needsTime && { key: 'date', label: 'Yeni gün', type: 'date', required: true },
        needsTime && { key: 'clock', label: 'Yeni saat', type: 'custom', required: true, wide: false,
          render: (draft, set) => clockInput(draft.clock || '', (value) => set(value), 'Yeni saat').node },
        ['confirm', 'reschedule'].includes(decision) && { key: 'room', label: 'Oda', maxLength: 40 },
        ['confirm', 'reschedule'].includes(decision) && { key: 'onlineUrl', label: 'Bağlantı (çevrimiçi)', maxLength: 300 },
        { key: 'reason', label: decision === 'decline' ? 'Gerekçe (talep edene gider)' : 'Not (isteğe bağlı)',
          type: 'textarea', required: decision === 'decline', maxLength: 300, wide: true },
      ],
      value: { date: todayIst(1), clock: '', room: meeting.room || '', onlineUrl: meeting.onlineUrl || '', reason: '' },
      submitLabel: MEETING_DECISIONS[decision][0],
      onSubmit: (draft, { key }) => {
        if (needsTime && !/^\d{2}:\d{2}$/.test(draft.clock || '')) throw new Error('Saati SS:DD biçiminde yazın.');
        const built = meetingDecisionBody(decision, { ...draft,
          startsAt: needsTime ? istanbulStamp(draft.date, draft.clock) : null });
        if (built.error) throw new Error(built.error);
        return app.api.post(`/v1/admin/coaching/meetings/${meeting.id}/decision`, built.body, { key });
      },
    });
    if (!result) return;
    meeting = { ...meeting, ...result };
    onChange?.(meeting);
    app.toast('Karar kaydedildi.', 'good');
    render();
  }

  async function editNote() {
    const items = (meeting.note?.actionItems || []).map((item) => ({ id: item.id, text: item.text, done: item.done }));
    const result = await formDialog(app, {
      title: 'Görüşme notu',
      description: 'Not bir koç notudur; görünürlüğü seçin. Eylem maddeleri sonraki görüşmede "geçen sefer '
        + 'kararlaştırılanlar" olarak çıkar.',
      wide: true,
      fields: [
        { key: 'text', label: 'Not', type: 'textarea', required: true, maxLength: 4000, wide: true },
        { key: 'visibility', label: 'Görünürlük', type: 'select', options: optionsOf(NOTE_VISIBILITY) },
        { key: 'actionItems', label: 'Eylem maddeleri (her satıra bir madde; bitenin başına ✓)', type: 'textarea',
          maxLength: 6000, wide: true },
      ],
      value: { text: meeting.note?.text || '', visibility: meeting.note?.visibility || 'private',
        actionItems: items.map((item) => `${item.done ? '✓ ' : ''}${item.text}`).join('\n') },
      submitLabel: 'Kaydet',
      onSubmit: (draft) => {
        const lines = String(draft.actionItems || '').split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 20);
        const actionItems = lines.map((line) => {
          const done = line.startsWith('✓');
          const text = line.replace(/^✓\s*/, '').slice(0, 300);
          const previous = items.find((item) => item.text === text);
          return { ...(previous?.id ? { id: previous.id } : {}), text, done };
        });
        return app.api.put(`/v1/admin/coaching/meetings/${meeting.id}/note`, { text: String(draft.text).trim(),
          visibility: draft.visibility || 'private', actionItems });
      },
    });
    if (!result) return;
    meeting = { ...meeting, ...result };
    onChange?.(meeting);
    app.toast('Not kaydedildi.', 'good');
    render();
  }

  async function convert(item, to) {
    const isTask = to === 'plan_task';
    const result = await formDialog(app, {
      title: isTask ? 'Göreve dönüştür' : 'Hedef önerisine dönüştür',
      description: `"${item.text}"`,
      fields: isTask ? [
        { key: 'date', label: 'Gün', type: 'date', required: true },
        { key: 'type', label: 'Tür', type: 'select', options: optionsOf(TASK_TYPES) },
        { key: 'estimatedMinutes', label: 'Süre (dk)', type: 'number', required: true, min: 5, max: 600 },
      ] : [
        { key: 'kind', label: 'Hedef', type: 'select',
          options: Object.entries(GOAL_KINDS).map(([value, [label, unit]]) => ({ value, label: `${label} (${unit})` })) },
        { key: 'targetValue', label: 'Hedef değer', type: 'number', required: true, min: 0 },
        { key: 'examType', label: 'Deneme türü (deneme neti için)', type: 'select',
          options: Object.entries(GOAL_EXAM_TYPES).map(([value, label]) => ({ value, label })) },
        { key: 'dueDate', label: 'Son tarih', type: 'date' },
      ],
      value: isTask ? { date: todayIst(1), type: 'free', estimatedMinutes: 30 }
        : { kind: 'weekly_questions', targetValue: '', examType: 'lgs', dueDate: '' },
      submitLabel: 'Dönüştür',
      onSubmit: (draft, { key }) => {
        let body;
        if (isTask) {
          body = { to, task: { date: draft.date, type: draft.type || 'free', estimatedMinutes: Number(draft.estimatedMinutes),
            title: item.text.slice(0, 120) } };
        } else {
          const built = goalBody({ ...draft, note: item.text });
          if (built.error) throw new Error(built.error);
          body = { to, goal: built.body };
        }
        return app.api.post(`/v1/admin/coaching/meetings/${meeting.id}/action-items/${item.id}/convert`, body, { key });
      },
    });
    if (!result) return;
    meeting = { ...meeting, ...result };
    onChange?.(meeting);
    app.toast(isTask ? 'Eylem maddesi göreve dönüştü.' : 'Eylem maddesi hedef önerisi oldu.', 'good');
    render();
  }

  try {
    meeting = { ...row, ...(await app.api.get(`/v1/admin/coaching/meetings/${row.id}`)) };
    render();
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openMeeting(app, row, onChange)));
  }
}
