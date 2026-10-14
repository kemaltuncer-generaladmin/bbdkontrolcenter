// BBD Akademi — Koçluk → Öğrenciler ve koçluk kartı (F-K5, F-K18, F-K19, F-K20, F-K22).
//
// Liste (`admin_listCoachingStudents`, ön hesaplı günlük özet): koç, sınıf,
// hedef sınav, risk süzgeci; sıra risk / uyum / ad / son etkinlik.
//
// Koçluk kartı (çekmece, `admin_getCoachingCard` — 360°): uyum, seri, bekleyen
// kontrol, açık soru, hafta (yayımlandı mı, görev/bitti), gecikmiş ve tekrar
// görevleri, video, mesaj, check-in, sonraki görüşme, hedefler, raf, devam.
// Eylemler: haftalık program, hedef öner, tebrik / rozet ver, öğrenciye özel
// check-in soruları, görüşme planla, koçluk dönem raporu (PDF, arka plan işi).
// İç sekmeler: hedef önerileri (geri çek), tebrikler, görüşmeler, sorular ve
// "Öğrencinin gördüğü" (öğrenci/veli koçluk görünümü `students_getCoachingOverview`).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, hintBox, kpiRow, skeletonRows, tabBar } from '../../ui-kit/layout.js';
import {
  actionButton, clockInput, confirmAction, errorBox, fmtDate, fmtDateTime, formDialog, istanbulStamp,
  kv, loader, openDrawer, optionsOf, pagedList, runAction, searchBox, selectBox, statusBadge, todayIst, toolbar,
} from './common.js';
import {
  EXAM_TREND, GOAL_EXAM_TYPES, GOAL_KINDS, GOAL_STATUS, MEETING_DURATIONS, MEETING_MODES, MEETING_PARTICIPANTS, MEETING_STATUS,
  REACTIONS, RISK, TERM_RESULT, badgeText, goalBody, goalText, pct,
} from './coaching-logic.js';
import { coachOptions, coachingJob, riskBadge, studentCell } from './coaching-shared.js';
import { questionsDialog } from './coaching-checkins.js';
import { problem } from './api.js';

const SORTS = { risk: 'Risk önce', adherence: 'Uyumu düşük önce', name: 'Ada göre', last_activity: 'Son etkinlik' };

export function tab(app, ctx) {
  const node = h('div', 'ak-stack');
  const filter = { coachId: '', q: '', classGroupId: '', examTarget: '', risk: '', sort: 'risk' };
  const coachSlot = h('div');
  const classSlot = h('div');
  const search = searchBox({ placeholder: 'Öğrenci ara', label: 'Öğrenci ara', onSearch: (text) => { filter.q = text; list.reload(); } });
  const exam = selectBox([{ value: '', label: 'Bütün sınavlar' }, { value: 'LGS', label: 'LGS' }, { value: 'YKS', label: 'YKS' }], '',
    (value) => { filter.examTarget = value; list.reload(); }, { label: 'Hedef sınav' });
  const risk = selectBox(optionsOf(Object.fromEntries(Object.entries(RISK).filter(([key]) => key !== 'none')),
    { empty: 'Bütün riskler' }), '', (value) => { filter.risk = value; list.reload(); }, { label: 'Risk' });
  const sort = selectBox(optionsOf(SORTS), 'risk', (value) => { filter.sort = value; list.reload(); }, { label: 'Sıra' });
  node.append(toolbar(search.node, coachSlot, classSlot, exam.node, risk.node, sort.node));
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachSlot.append(selectBox(options, '', (value) => { filter.coachId = value; list.reload(); }, { label: 'Koç' }).node);
  });
  app.lookups.options('classGroups', { empty: 'Bütün sınıflar' }).then((options) => {
    classSlot.append(selectBox(options, '', (value) => { filter.classGroupId = value; list.reload(); }, { label: 'Sınıf' }).node);
  }).catch(() => {});

  const list = pagedList({
    app,
    path: '/v1/admin/coaching/students',
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    onRow: (row) => openCard(app, row.student, ctx),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: (row) => studentCell(row.student) },
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1fr)', cell: (row) => row.coach?.displayName || '—' },
      { key: 'adherence', label: 'Uyum (7 gün)', width: '110px', align: 'num', cell: (row) => pct(row.stats?.adherence7dPercent) },
      { key: 'pending', label: 'Bekleyen', width: '150px', cell: (row) => `${row.stats?.pendingReviews ?? 0} kontrol · ${row.stats?.openQuestions ?? 0} soru` },
      { key: 'week', label: 'Bu hafta', width: '130px', cell: (row) => (row.stats?.weekPublished ? badge('Yayımlandı', 'good')
        : badge('Yayımlanmadı', 'warn')) },
      { key: 'exam', label: 'Son deneme', width: '110px', cell: (row) => (row.stats?.lastExamNet === null || row.stats?.lastExamNet === undefined
        ? '—' : `${Number(row.stats.lastExamNet).toLocaleString('tr-TR')} ${EXAM_TREND[row.stats.examTrend] || ''}`) },
      { key: 'last', label: 'Son etkinlik', width: '140px', cell: (row) => (row.stats?.lastActivityAt ? fmtDateTime(row.stats.lastActivityAt) : '—') },
      { key: 'risk', label: 'Risk', width: '100px', cell: (row) => riskBadge(row.stats?.risk) },
    ],
    empty: { title: 'Koçlu öğrenci yok', text: 'Koç ataması Koçlar sekmesindeki "Toplu koç ata" ya da öğrenci kartından yapılır.' },
  });
  node.append(list.node);
  let started = false;
  return {
    node,
    shown() { if (!started) { started = true; list.reload(); } },
    destroy() { list.destroy(); search.destroy(); },
  };
}

// ---------------------------------------------------------------- kart

/** Öğrencinin koçluk kartı (çekmece). `student`: {id, displayName, …}. */
export function openCard(app, student, ctx = {}) {
  const studentId = student?.id;
  if (!studentId) return null;
  const view = openDrawer(app, { title: student.displayName || 'Koçluk kartı', subtitle: 'Koçluk kartı' });
  const bodyTabs = [
    { key: 'summary', label: 'Özet' },
    app.can('admin_listGoalProposals') && { key: 'goals', label: 'Hedef önerileri' },
    app.can('students_listKudos') && { key: 'kudos', label: 'Tebrikler' },
    app.can('students_listCoachMeetings') && { key: 'meetings', label: 'Görüşmeler' },
    app.can('students_listQuestions') && { key: 'questions', label: 'Sorular' },
    app.can('students_listTopicLibraryItems') && { key: 'library', label: 'Kütüphane' },
    app.can('students_getCoachingOverview') && { key: 'overview', label: 'Öğrencinin gördüğü' },
  ].filter(Boolean);
  const slot = h('div', 'ak-coach-card');
  const panes = new Map();
  const show = (key) => {
    for (const [name, pane] of panes) pane.node.hidden = name !== key;
    if (!panes.has(key)) {
      const pane = buildPane(key);
      panes.set(key, pane);
      slot.append(pane.node);
      pane.reload?.();
    }
  };
  let card = null;
  const buildPane = (key) => {
    switch (key) {
      case 'goals': return goalsPane(app, studentId);
      case 'kudos': return kudosPane(app, studentId);
      case 'meetings': return meetingsPane(app, studentId);
      case 'questions': return questionsPane(app, studentId);
      case 'library': return libraryPane(app, studentId);
      case 'overview': return overviewPane(app, studentId);
      default: return loader(() => app.api.get(`/v1/admin/students/${studentId}/coaching-card`), (data) => {
        card = data;
        if (data?.student?.displayName) view.setTitle(data.student.displayName);
        return summary(app, data, { studentId, ctx, view, refresh: () => panes.get('summary')?.reload() });
      }, { rows: 6, columns: 3 });
    }
  };
  const bar = tabBar(bodyTabs, 'summary', show);
  view.body.replaceChildren(bar.node, slot);
  show('summary');
  return { view, card: () => card };
}

function summary(app, card, { studentId, ctx, view, refresh }) {
  const stats = card.stats || {};
  const parts = [];
  parts.push(kpiRow([
    { label: 'Uyum (7 gün)', value: pct(stats.adherence7dPercent) },
    { label: 'Seri', value: `${stats.streakDays ?? 0} gün` },
    { label: 'Kontrol bekleyen', value: String(stats.pendingReviews ?? 0), tone: stats.pendingReviews ? 'warn' : '' },
    { label: 'Açık soru', value: String(stats.openQuestions ?? 0), tone: stats.openQuestions ? 'warn' : '' },
    { label: 'Risk', value: (RISK[stats.risk] || RISK.none)[0], tone: stats.risk === 'high' ? 'bad' : '' },
  ]));
  const week = card.week || {};
  parts.push(kv([
    ['Koç', card.coach ? `${card.coach.displayName}${card.coach.subjects?.length ? ` · ${card.coach.subjects.map((s) => s.name).join(', ')}` : ''}` : 'Koçu yok'],
    ['Bu hafta', `${fmtDate(week.weekStart)} · ${week.published ? 'yayımlandı' : 'yayımlanmadı'} · ${week.doneCount ?? 0}/${week.taskCount ?? 0} görev`],
    ['Görevler', `${card.tasks?.pendingReview ?? 0} kontrol bekliyor · ${card.tasks?.delayed ?? 0} gecikmiş · ${card.tasks?.redo ?? 0} tekrar`],
    ['Videolar', `${card.videos?.assigned ?? 0} atandı · ${card.videos?.completedMeasured ?? 0} izlendi · ${card.videos?.completedSelfReported ?? 0} beyan`],
    ['Sorular', `${card.questions?.open ?? 0} açık · son 30 günde ${card.questions?.answered30d ?? 0} yanıt`],
    ['Mesajlar', `${card.messages?.unread ?? 0} okunmamış${card.messages?.lastMessageAt ? ` · son ${fmtDateTime(card.messages.lastMessageAt)}` : ''}`],
    ['Check-in', `${fmtDate(card.checkin?.weekStart)} · ${card.checkin?.submitted ? 'dolduruldu' : 'doldurulmadı'}${card.checkin?.commented ? ' · yorumlandı' : ''}`],
    ['Sonraki görüşme', card.nextMeeting ? `${fmtDateTime(card.nextMeeting.startsAt)} · ${MEETING_MODES[card.nextMeeting.mode] || ''}` : '—'],
    ['Hedefler', `${card.goals?.active ?? 0} etkin · ${card.goals?.proposalsPending ?? 0} öneri yanıt bekliyor`],
    ['Devam (30 gün)', `${pct(card.attendance?.ratePercent30d)} · ${card.attendance?.absent30d ?? 0} devamsız · ${card.attendance?.late30d ?? 0} geç`],
    ['Son deneme', stats.lastExamNet === null || stats.lastExamNet === undefined ? '—' : `${stats.lastExamNet} net ${EXAM_TREND[stats.examTrend] || ''}`],
  ]));
  if (card.resources?.length) {
    const shelf = h('div', 'ak-coach-shelf');
    for (const item of card.resources) {
      const line = h('div', 'ak-coach-shelf-row');
      const meter = h('span', 'ak-coach-meter');
      const fill = h('span', 'ak-coach-meter-fill');
      fill.style.width = `${Math.max(0, Math.min(100, Number(item.progressPercent) || 0))}%`;
      meter.append(fill);
      line.append(h('span', '', item.title), meter, h('small', 'ak-muted', pct(item.progressPercent)));
      shelf.append(line);
    }
    parts.push(h('h4', 'ak-coach-sub', 'Raf'), shelf);
  }
  parts.push(h('small', 'ak-muted', `Hesaplandı: ${fmtDateTime(card.computedAt)}`));
  const student = card.student || { id: studentId };
  const tools = h('div', 'ak-row-actions');
  tools.append(
    ...(app.can('admin_getCoachWeek') ? [button('Haftalık program', { variant: 'primary', onClick: () => {
      view.close();
      ctx.go?.('week', { student });
    } })] : []),
    actionButton(app, 'admin_createGoalProposal', 'Hedef öner', { variant: 'ghost',
      onClick: async () => { if (await goalDialog(app, studentId, { examTarget: student.examTarget })) refresh(); } }),
    actionButton(app, 'admin_giveKudos', 'Tebrik / rozet ver', { variant: 'ghost',
      onClick: () => kudosDialog(app, student) }),
    actionButton(app, 'admin_putStudentCheckinQuestions', 'Check-in soruları', { variant: 'ghost',
      onClick: () => studentQuestions(app, student) }),
    actionButton(app, 'admin_createCoachMeeting', 'Görüşme planla', { variant: 'ghost',
      onClick: async () => { if (await meetingDialog(app, student)) refresh(); } }),
    actionButton(app, 'admin_requestCoachingReport', 'Dönem raporu (PDF)', { variant: 'ghost',
      onClick: () => reportDialog(app, student) }),
  );
  parts.push(tools);
  return parts;
}

// -------------------------------------------------------------- pencereler

/** Hedef önerisi penceresi. Dönüş: oluşan öneri | null. */
export async function goalDialog(app, studentId, { examTarget } = {}) {
  // Deneme neti hedefinde varsayılan deneme türü öğrencinin hedef sınavından (YKS → TYT, değilse LGS).
  const examTypeDefault = examTarget === 'YKS' ? 'tyt' : 'lgs';
  const [subjects, shelf] = await Promise.all([
    app.lookups.options('subjects', { empty: 'Ders seçin (ders neti için)' }).catch(() => []),
    app.can('admin_getTaskBuilderContext')
      ? app.api.get(`/v1/admin/students/${studentId}/task-builder`).then((data) => data?.shelf || []).catch(() => [])
      : Promise.resolve([]),
  ]);
  return formDialog(app, {
    title: 'Hedef öner',
    description: 'Öğrenciye bildirim gider; kabul ederse hedeflerine yazılır ve ilerleme kendiliğinden hesaplanır.',
    fields: [
      { key: 'kind', label: 'Hedef', type: 'select', required: true,
        options: Object.entries(GOAL_KINDS).map(([value, [label, unit]]) => ({ value, label: `${label} (${unit})` })) },
      { key: 'targetValue', label: 'Hedef değer', type: 'number', required: true, min: 0 },
      { key: 'subjectId', label: 'Ders', type: 'select', options: subjects },
      { key: 'examType', label: 'Deneme türü (deneme neti için)', type: 'select',
        options: Object.entries(GOAL_EXAM_TYPES).map(([value, label]) => ({ value, label })) },
      { key: 'resourceId', label: 'Kaynak (kaynağı bitirme için)', type: 'select',
        options: [{ value: '', label: shelf.length ? 'Kaynak seçin' : 'Rafta kaynak yok' },
          ...shelf.map((item) => ({ value: item.resourceId, label: item.title }))] },
      { key: 'dueDate', label: 'Son tarih', type: 'date' },
      { key: 'note', label: 'Not (öğrenci görür)', type: 'textarea', maxLength: 500, wide: true },
    ],
    value: { kind: 'weekly_questions', targetValue: '', subjectId: '', resourceId: '', examType: examTypeDefault,
      dueDate: '', note: '' },
    submitLabel: 'Öner',
    onSubmit: (draft, { key }) => {
      const built = goalBody(draft);
      if (built.error) throw new Error(built.error);
      return app.api.post(`/v1/admin/students/${studentId}/goal-proposals`, built.body, { key })
        .then((result) => { app.toast('Hedef önerildi; öğrenciye bildirim gitti.', 'good'); return result; });
    },
  });
}

/** Tebrik / koç rozeti penceresi. */
export async function kudosDialog(app, student) {
  let badges = [];
  try {
    badges = (await app.api.getCached('/v1/admin/coaching/badges'))?.items?.filter((item) => item.active !== false) || [];
  } catch {
    badges = [];
  }
  return formDialog(app, {
    title: `Tebrik — ${student.displayName || ''}`.trim(),
    description: 'Öğrenciye bildirim gider. Rozet verirken gerekçe zorunludur.',
    fields: [
      { key: 'text', label: 'Mesaj', type: 'textarea', required: true, maxLength: 280, wide: true },
      { key: 'sticker', label: 'Çıkartma', type: 'select',
        options: [{ value: '', label: 'Yok' }, ...REACTIONS.map((item) => ({ value: item, label: item }))] },
      { key: 'badgeId', label: 'Koç rozeti', type: 'select',
        options: [{ value: '', label: badges.length ? 'Rozet yok' : 'Tanımlı rozet yok' },
          ...badges.map((item) => ({ value: item.id, label: badgeText(item) }))] },
      { key: 'reason', label: 'Rozet gerekçesi', type: 'textarea', maxLength: 300, wide: true,
        validate: (value, draft) => (draft.badgeId && !String(value || '').trim() ? 'Rozet için gerekçe yazın.' : null) },
    ],
    value: { text: 'Harika gidiyorsun, böyle devam!', sticker: '', badgeId: '', reason: '' },
    submitLabel: 'Gönder',
    onSubmit: (draft, { key }) => {
      const body = { text: String(draft.text).trim().slice(0, 280) };
      if (draft.sticker) body.sticker = draft.sticker;
      if (draft.badgeId) { body.badgeId = draft.badgeId; body.reason = String(draft.reason || '').trim().slice(0, 300); }
      return app.api.post(`/v1/admin/students/${student.id}/kudos`, body, { key })
        .then((result) => { app.toast('Tebrik gönderildi.', 'good'); return result; });
    },
  });
}

async function studentQuestions(app, student) {
  const path = `/v1/admin/students/${student.id}/checkin-questions`;
  let current = null;
  try {
    current = await app.api.get(path);
  } catch (failure) {
    app.toast(problem(failure), 'bad');
    return;
  }
  const saved = await questionsDialog(app, { title: `Öğrenciye özel check-in soruları — ${student.displayName || ''}`.trim(),
    max: 3, questions: current?.questions || [],
    description: 'Kurum sorularına ek 0–3 soru; yalnız bu öğrencinin pazar öz değerlendirmesinde çıkar.',
    save: (questions) => app.api.put(path, { questions, updatedAt: current?.updatedAt ?? null }) });
  if (saved) app.toast('Öğrenciye özel sorular kaydedildi.', 'good');
}

/** Görüşme planla (`admin_createCoachMeeting`). */
export function meetingDialog(app, student) {
  return formDialog(app, {
    title: `Görüşme planla — ${student.displayName || ''}`.trim(),
    description: 'Görüşme öğrenciye (ve seçilirse veliye) bildirilir; koçun takviminde aynı saatte görüşme varsa '
      + 'kayıt reddedilir.',
    fields: [
      { key: 'date', label: 'Gün', type: 'date', required: true },
      { key: 'clock', label: 'Saat', type: 'custom', required: true, wide: false,
        render: (draft, set) => clockInput(draft.clock || '', (value) => set(value), 'Saat').node },
      { key: 'durationMinutes', label: 'Süre', type: 'select', required: true,
        options: MEETING_DURATIONS.map((minutes) => ({ value: String(minutes), label: `${minutes} dk` })) },
      { key: 'mode', label: 'Biçim', type: 'select', required: true, options: optionsOf(MEETING_MODES) },
      { key: 'participants', label: 'Katılımcı', type: 'select', options: optionsOf(MEETING_PARTICIPANTS) },
      { key: 'room', label: 'Oda (yüz yüze)', maxLength: 40 },
      { key: 'onlineUrl', label: 'Bağlantı (çevrimiçi)', maxLength: 300,
        validate: (value) => (value && !/^https?:\/\/\S+$/i.test(String(value).trim()) ? 'https:// ile başlayan bağlantı yazın.' : null) },
      { key: 'topic', label: 'Konu', maxLength: 200, wide: true },
    ],
    value: { date: todayIst(1), clock: '', durationMinutes: '30', mode: 'in_person', participants: 'student', room: '',
      onlineUrl: '', topic: '' },
    submitLabel: 'Planla',
    onSubmit: (draft, { key }) => {
      if (!/^\d{2}:\d{2}$/.test(draft.clock || '')) throw new Error('Saati SS:DD biçiminde yazın.');
      const body = { startsAt: istanbulStamp(draft.date, draft.clock), durationMinutes: Number(draft.durationMinutes),
        mode: draft.mode, participants: draft.participants || 'student' };
      if (draft.room) body.room = String(draft.room).trim();
      if (draft.onlineUrl) body.onlineUrl = String(draft.onlineUrl).trim();
      if (draft.topic) body.topic = String(draft.topic).trim();
      return app.api.post(`/v1/admin/students/${student.id}/coach-meetings`, body, { key })
        .then((result) => { app.toast('Görüşme planlandı.', 'good'); return result; });
    },
  });
}

async function reportDialog(app, student) {
  const terms = await app.lookups.options('terms').catch(() => []);
  const form = await formDialog(app, {
    title: `Koçluk dönem raporu — ${student.displayName || ''}`.trim(),
    description: 'Koç yorumu + uyum, soru, deneme eğilimi, hedefler ve görüşmeler; PDF arka planda hazırlanır.',
    fields: [
      { key: 'termId', label: 'Dönem', type: 'select', required: true,
        options: terms.length ? terms : [{ value: '', label: 'Dönem tanımlı değil' }] },
      { key: 'coachComment', label: 'Koç yorumu', type: 'textarea', maxLength: 2000, wide: true },
      { key: 'mergeWithEvaluation', label: 'Gelişim karnesiyle birleştir', type: 'checkbox' },
    ],
    value: { termId: terms[0]?.value || '', coachComment: '', mergeWithEvaluation: false },
    submitLabel: 'Raporu hazırla',
    onSubmit: async (draft) => draft,
  });
  if (!form) return;
  await coachingJob(app, () => app.api.post(`/v1/admin/students/${student.id}/coaching-report`, {
    termId: form.termId, coachComment: String(form.coachComment || '').trim() || null,
    mergeWithEvaluation: Boolean(form.mergeWithEvaluation) }), { name: 'kocluk-raporu', label: 'Koçluk raporu hazırlanıyor…' });
}

// --------------------------------------------------------------- iç sekmeler

function goalsPane(app, studentId) {
  const list = pagedList({
    app,
    path: `/v1/admin/students/${studentId}/goal-proposals`,
    query: {},
    columns: [
      { key: 'goal', label: 'Hedef', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', goalText(row)), h('small', '', [row.dueDate ? `son ${fmtDate(row.dueDate)}` : '',
          row.proposedBy?.displayName ? `öneren ${row.proposedBy.displayName}` : '', row.note || ''].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'status', label: 'Durum', width: '130px', cell: (row) => statusBadge(GOAL_STATUS, row.status) },
      { key: 'progress', label: 'İlerleme', width: '110px', cell: (row) => (row.progress ? pct(row.progress.percent) : '—') },
      { key: 'result', label: 'Sonuç', width: '100px', cell: (row) => (row.termResult ? statusBadge(TERM_RESULT, row.termResult) : '—') },
      { key: 'act', label: '', width: '120px', cell: (row) => (['proposed', 'discussing'].includes(row.status)
        ? actionButton(app, 'admin_withdrawGoalProposal', 'Geri çek', { variant: 'ghost', onClick: async (event) => {
          event.stopPropagation();
          if (!await confirmAction(app, { title: 'Öneriyi geri çek', description: 'Öğrenci öneriyi artık görmez.',
            confirmLabel: 'Geri çek', danger: true })) return;
          const next = await runAction(app, () => app.api.post(
            `/v1/admin/students/${studentId}/goal-proposals/${row.id}/withdraw`, {}), { success: 'Öneri geri çekildi.' });
          if (next) list.replace((item) => item.id === row.id, next);
        } }) : '') },
    ],
    empty: { title: 'Hedef önerisi yok', text: '"Hedef öner" ile öğrenciye hedef önerebilirsiniz.' },
  });
  const node = h('div', 'ak-stack');
  node.append(list.node);
  return { node, reload: () => list.reload() };
}

function kudosPane(app, studentId) {
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/kudos`,
    query: {},
    columns: [
      { key: 'text', label: 'Tebrik', width: 'minmax(0, 2fr)', cell: (row) => `${row.sticker ? `${row.sticker} ` : ''}${row.text}` },
      { key: 'badge', label: 'Rozet', width: 'minmax(0, 1fr)', cell: (row) => (row.badge
        ? `${badgeText(row.badge)}${row.reason ? ` — ${row.reason}` : ''}` : '—') },
      { key: 'from', label: 'Veren', width: 'minmax(0, 1fr)', cell: (row) => row.from?.displayName || '—' },
      { key: 'createdAt', label: 'Tarih', width: '140px', cell: (row) => fmtDateTime(row.createdAt) },
    ],
    empty: { title: 'Tebrik yok', text: 'Koçun ve kurumun verdiği tebrikler burada görünür.' },
  });
  const node = h('div', 'ak-stack');
  node.append(list.node);
  return { node, reload: () => list.reload() };
}

function meetingsPane(app, studentId) {
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/coach-meetings`,
    query: {},
    onRow: app.can('admin_getCoachMeeting')
      ? (row) => import('./coaching-meetings.js').then((module) => module.openMeeting(app, row, () => list.reload())) : undefined,
    columns: [
      { key: 'startsAt', label: 'Zaman', width: '150px', cell: (row) => fmtDateTime(row.startsAt) },
      { key: 'mode', label: 'Biçim', width: '110px', cell: (row) => MEETING_MODES[row.mode] || row.mode },
      { key: 'topic', label: 'Konu', width: 'minmax(0, 1.5fr)', cell: (row) => row.topic || '—' },
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1fr)', cell: (row) => row.coach?.displayName || '—' },
      { key: 'status', label: 'Durum', width: '120px', cell: (row) => statusBadge(MEETING_STATUS, row.status) },
    ],
    empty: { title: 'Görüşme yok', text: '"Görüşme planla" ile koç görüşmesi açabilirsiniz.' },
  });
  const node = h('div', 'ak-stack');
  node.append(list.node);
  return { node, reload: () => list.reload() };
}

const QUESTION_STATUS = { open: ['Açık', 'warn'], answered: ['Yanıtlandı', 'good'], closed: ['Kapalı', 'dim'],
  forwarded: ['Yönlendirildi', 'info'] };

function questionsPane(app, studentId) {
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/questions`,
    query: {},
    onRow: (row) => openStudentQuestion(app, studentId, row),
    columns: [
      { key: 'askedAt', label: 'Soruldu', width: '140px', cell: (row) => fmtDateTime(row.askedAt) },
      { key: 'text', label: 'Soru', width: 'minmax(0, 2fr)', cell: (row) => row.text || (row.images?.length ? `${row.images.length} görsel` : '—') },
      { key: 'coach', label: 'Hoca', width: 'minmax(0, 1fr)', cell: (row) => (row.forwardedTo || row.coach)?.displayName || '—' },
      { key: 'status', label: 'Durum', width: '120px', cell: (row) => (row.overdue && row.status === 'open'
        ? badge('Gecikti', 'bad') : statusBadge(QUESTION_STATUS, row.status)) },
    ],
    empty: { title: 'Soru yok', text: 'Öğrencinin "Hocaya sor" soruları burada görünür.' },
  });
  const node = h('div', 'ak-stack');
  node.append(list.node, hintBox('Yanıt yazmak için Bugün → Hoca soruları ekranını kullanın.'));
  return { node, reload: () => list.reload() };
}

async function openStudentQuestion(app, studentId, row) {
  const view = openDrawer(app, { title: 'Soru', subtitle: fmtDateTime(row.askedAt) });
  view.body.replaceChildren(skeletonRows(4, 2));
  try {
    const question = await app.api.get(`/v1/students/${studentId}/questions/${row.id}`);
    const context = question.context || {};
    view.body.replaceChildren(kv([
      ['Soru', question.text || '—'],
      ['Bağlam', [context.subject?.name, context.topic?.name, context.resource?.title, context.page ? `s. ${context.page}` : '',
        context.questionNo ? `${context.questionNo}. soru` : ''].filter(Boolean).join(' · ') || '—'],
      ['Hoca', (question.forwardedTo || question.coach)?.displayName || '—'],
      ['Durum', statusBadge(QUESTION_STATUS, question.status)],
      ['İlk yanıt', question.firstAnsweredAt ? fmtDateTime(question.firstAnsweredAt) : '—'],
      ['Öğrenci geri bildirimi', question.studentFeedback === 'understood' ? 'Anladım'
        : question.studentFeedback === 'not_understood' ? 'Hâlâ anlamadım' : '—'],
    ]), button('Hoca soruları ekranında aç', { variant: 'ghost', onClick: () => { view.close(); app.go('questions'); } }));
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openStudentQuestion(app, studentId, row)));
  }
}

const LIBRARY_KIND_TEXT = { youtube_video: 'Video', youtube_clip: 'Video kesiti', youtube_playlist: 'Oynatma listesi',
  web_link: 'Bağlantı', file: 'Dosya', text_note: 'Not', task_pack: 'Görev paketi' };

/** Öğrencinin konu kütüphanesi (`students_listTopicLibraryItems`): koçun ve kurumun paylaştığı öğeler. */
function libraryPane(app, studentId) {
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/library`,
    query: {},
    columns: [
      { key: 'title', label: 'Öğe', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.title), h('small', '', (row.topics || []).map((topic) => topic.name).join(', ')));
        return cell;
      } },
      { key: 'kind', label: 'Tür', width: '140px', cell: (row) => LIBRARY_KIND_TEXT[row.kind] || row.kind },
      { key: 'scope', label: 'Kaynak', width: '140px', cell: (row) => (row.scope === 'institution' ? 'Kurum' : 'Koç') },
    ],
    empty: { title: 'Kütüphane boş', text: 'Öğrencinin konularına bağlı kurum ve koç öğeleri burada görünür.' },
  });
  const node = h('div', 'ak-stack');
  node.append(hintBox('Öğrencinin uygulamada konu sayfasında gördüğü videolar, bağlantılar ve notlar.'), list.node);
  return { node, reload: () => list.reload() };
}

function overviewPane(app, studentId) {
  const node = h('div', 'ak-stack');
  const view = loader(() => app.api.get(`/v1/students/${studentId}/coaching`), (data) => {
    const parts = [hintBox('Öğrencinin ve velinin uygulamadaki "Koçum" ekranında gördüğü özet (veli görev atayamaz).')];
    parts.push(kv([
      ['Koç', data.coach?.displayName || 'Koçu yok'],
      ['Hafta', `${fmtDate(data.week?.weekStart)} · ${data.week?.published ? 'yayımlandı' : 'yayımlanmadı'} · ${data.week?.doneCount ?? 0}/${data.week?.taskCount ?? 0} bitti (${pct(data.week?.completionPercent)})`],
      ['Videolar', `${data.videos?.assigned ?? 0} atandı · ${data.videos?.completedMeasured ?? 0} izlendi`],
      ['Kontroller', `${data.reviews?.approved ?? 0} onay · ${data.reviews?.redo ?? 0} tekrar · ${data.reviews?.pending ?? 0} bekliyor`],
      ['Sorular', `${data.questions?.asked ?? 0} soruldu · ${data.questions?.answered ?? 0} yanıtlandı`],
      ['Paylaşılan koç notu', String(data.sharedNotes ?? 0)],
      ['Sonraki görüşme', data.nextMeeting ? fmtDateTime(data.nextMeeting.startsAt) : '—'],
      ['Hedefler', `${data.goals?.active ?? 0} etkin · ${data.goals?.proposalsPending ?? 0} öneri`],
    ]));
    if (app.can('students_listGoalProposals')) {
      const goals = h('div', 'ak-stack');
      parts.push(h('h4', 'ak-coach-sub', 'Öğrencinin gördüğü hedef önerileri'), goals);
      app.api.get(`/v1/students/${studentId}/goal-proposals`, { limit: 20 }).then((page) => {
        const items = page?.items || [];
        if (!items.length) { goals.append(h('p', 'ak-muted', 'Öneri yok.')); return; }
        for (const goal of items) {
          const line = h('div', 'ak-coach-shelf-row');
          line.append(h('span', '', goalText(goal)), statusBadge(GOAL_STATUS, goal.status));
          goals.append(line);
        }
      }).catch((failure) => goals.append(alertBox(problem(failure), 'bad')));
    }
    return parts;
  });
  node.append(view.node);
  return { node, reload: view.reload };
}

