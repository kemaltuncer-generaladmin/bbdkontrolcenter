// BBD Akademi — Koçluk → Haftalık program (F-K6 görev oluşturucu, F-K7 hafta, F-K8 şablonla atama).
//
// Öğrenci × hafta GENİŞ ızgara (`admin_getCoachWeek`): yedi gün sütunu; her
// günde ders programı ve etütler, tatil ve çalışma saatleri GÖLGE katmandır
// (değiştirilmez), üstünde görevler: koç görevi ("Koç görevi", taslaksa
// "Taslak") ve öğrencinin kendi işi ("Öğrencinin kendi işi", salt okunur).
// Gün başlığı planlanan süre / kapasite ("Kapasite aşıldı" uyarıdır, engel
// değil) ve soru sayısını gösterir; altta ders başına dakika.
//
// Her değişiklik TEK istekle kaydedilir (`admin_saveCoachWeek`, iyimser kilit
// `expectedVersion`; başka biri değiştirdiyse hafta yeniden yüklenir): görev
// ekle / düzenle / başka güne taşı / sil. Yeni görev TASLAKTIR — öğrenci
// "Yayımla"ya (`admin_publishCoachWeek`, bildirimli) dek görmez.
// Görev oluşturucu (`admin_getTaskBuilderContext`): raf (ilerleme, sıradaki
// bölüm), zayıf konular, tekrar zamanı gelen konular; kaynak bölümleri
// (`admin_getStudentResourceSections`), YouTube videosu / kütüphane (F-K9),
// talimat, soru aralığı, öncelik, kanıt kuralı, başarı ölçütü, ek (foto / PDF,
// `admin_uploadCoachingFile`, en çok 3).
// Hafta eylemleri: geçen haftadan devret (`admin_carryOverCoachWeek`), başka
// öğrenci / haftaya kopyala (`admin_copyCoachWeek`, önce deneme), PDF
// (`admin_requestCoachWeekPdf`, arka plan işi). Şablonla toplu atama
// (`admin_previewTemplateAssignment` → `admin_applyTemplateAssignment`):
// kurum plan şablonu → sınıf / grup / öğrenci, önizlemeli.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, emptyState, hintBox, kpiRow, skeletonRows } from '../../ui-kit/layout.js';
import { AkademiError, problem } from './api.js';
import {
  DAY_LABEL, WEEKDAYS, actionButton, confirmAction, errorBox, fileDrop, fmtDate, formDialog, multiChips, openDrawer, optionsOf,
  runAction, selectBox, staticList, statusBadge, studentPicker, todayIst, toolbar, weekStart,
} from './common.js';
import { loadTargets, targetsField, topicPicker } from './plan-tasks.js';
import {
  COPY_STATUS, PRIORITY, PROOF_PHOTO, TASK_TYPES, UNRESOLVED, addDaysIso, isCoachTask, isDraft, minutesText, pct,
  saveSummary, taskDetail, taskForm, taskInput, weekLabel, weekOps,
} from './coaching-logic.js';
import { coachingJob, setEnabled, studentCell } from './coaching-shared.js';
import { videoCard, videoDialog } from './coaching-video.js';

const STATUS = { planned: ['Planlandı', 'dim'], done: ['Bitti', 'good'], partial: ['Kısmen', 'warn'],
  skipped: ['Yapılmadı', 'bad'], moved: ['Taşındı', 'dim'] };
const SHORT_DAY = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));
const LESSON_KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik', break: 'Ara' };

export function tab(app) {
  const node = h('div', 'ak-stack');
  const state = { student: null, week: weekStart(todayIst(0)), data: null, busy: false };

  // --- üst şerit: öğrenci, hafta, eylemler
  const who = h('div', 'ak-coach-who');
  const picker = studentPicker(app, { placeholder: 'Öğrenci seç (ad, okul no)', onPick: (student) => choose(student) });
  const label = h('b', 'ak-coach-weeklabel', weekLabel(state.week));
  const prev = button('‹', { variant: 'ghost', title: 'Önceki hafta', onClick: () => move(-7) });
  const next = button('›', { variant: 'ghost', title: 'Sonraki hafta', onClick: () => move(7) });
  const today = button('Bu hafta', { variant: 'ghost', onClick: () => { state.week = weekStart(todayIst(0)); paintWeek(); load(); } });
  prev.setAttribute('aria-label', 'Önceki hafta');
  next.setAttribute('aria-label', 'Sonraki hafta');

  const add = actionButton(app, 'admin_saveCoachWeek', 'Görev ekle', { variant: 'primary', onClick: () => editTask(null) });
  const publish = actionButton(app, 'admin_publishCoachWeek', 'Yayımla', { variant: 'primary', onClick: () => publishWeek() });
  const carry = actionButton(app, 'admin_carryOverCoachWeek', 'Geçen haftadan devret', { variant: 'ghost', onClick: () => carryOver() });
  const copy = actionButton(app, 'admin_copyCoachWeek', 'Kopyala', { variant: 'ghost', onClick: () => copyWeek() });
  const pdf = actionButton(app, 'admin_requestCoachWeekPdf', 'PDF', { variant: 'ghost', onClick: () => weekPdf() });
  const template = actionButton(app, 'admin_previewTemplateAssignment', 'Şablonla toplu ata', { variant: 'ghost',
    onClick: () => templateAssignDialog(app, { onDone: () => load() }) });
  const studentActions = [add, publish, carry, copy, pdf];
  node.append(toolbar(picker.node, who), toolbar(prev, label, next, today, h('span', 'kit-spacer'), ...studentActions, template));

  const summary = h('div', 'ak-stack');
  const grid = h('div', 'ak-coach-week');
  node.append(summary, grid);

  function paintWeek() { label.textContent = weekLabel(state.week); }
  function move(days) { state.week = addDaysIso(state.week, days); paintWeek(); load(); }

  function choose(student) {
    if (!student?.id) return;
    state.student = student;
    who.replaceChildren(badge(student.displayName || 'Öğrenci', 'info'));
    load();
  }

  function paintIdle() {
    for (const control of studentActions) setEnabled(control, false);
    summary.replaceChildren();
    grid.replaceChildren(emptyState({ title: 'Öğrenci seçin',
      text: 'Haftalık program öğrenci başınadır. Öğrenciyi yukarıdan arayın ya da Öğrenciler sekmesinde kartından açın.' }));
  }

  let token = 0;
  async function load() {
    if (!state.student) { paintIdle(); return; }
    const mine = ++token;
    grid.replaceChildren(skeletonRows(6, 7));
    try {
      const data = await app.api.get(`/v1/admin/students/${state.student.id}/coach-week`, { week: state.week });
      if (mine !== token) return;
      render(data);
    } catch (failure) {
      if (mine !== token) return;
      summary.replaceChildren();
      grid.replaceChildren(errorBox(failure, () => load()));
    }
  }

  function render(data) {
    state.data = data;
    if (data.student?.displayName) who.replaceChildren(badge(data.student.displayName, 'info'));
    for (const control of [add, carry, copy, pdf]) setEnabled(control, true);
    setEnabled(publish, data.draftCount > 0 || !data.published);
    const tasks = data.days.flatMap((day) => day.tasks || []);
    const coachTasks = tasks.filter(isCoachTask);
    const planned = data.days.reduce((total, day) => total + (day.plannedMinutes || 0), 0);
    const questions = data.days.reduce((total, day) => total + (day.plannedQuestions || 0), 0);
    summary.replaceChildren(kpiRow([
      { label: 'Durum', value: data.published ? 'Yayımlandı' : 'Yayımlanmadı', tone: data.published ? 'good' : 'warn' },
      { label: 'Taslak görev', value: String(data.draftCount ?? 0), tone: data.draftCount ? 'warn' : '' },
      { label: 'Koç görevi', value: String(coachTasks.length) },
      { label: 'Bitti', value: `${tasks.filter((task) => task.status === 'done').length} / ${tasks.length}` },
      { label: 'Planlanan', value: minutesText(planned) },
      { label: 'Soru', value: String(questions) },
    ]));
    if (data.subjectMinutes?.length) {
      summary.append(h('small', 'ak-muted', `Ders başına: ${data.subjectMinutes.map((item) => `${item.subject?.shortName
        || item.subject?.name} ${minutesText(item.minutes)}`).join(' · ')}`));
    }
    if (data.draftCount) {
      summary.append(hintBox('Taslak görevleri öğrenci görmez. Hazır olunca "Yayımla" ile öğrenciye bildirim gider.'));
    }
    grid.replaceChildren(...data.days.map((day) => dayColumn(day)));
  }

  function dayColumn(day) {
    const column = h('div', `ak-coach-day${day.holiday ? ' holiday' : ''}${day.overCapacity ? ' over' : ''}`);
    const head = h('div', 'ak-coach-day-head');
    head.append(h('b', '', `${DAY_LABEL[day.dayOfWeek] || ''} ${fmtDate(day.date).slice(0, 5)}`));
    const load = day.capacityMinutes ? `${minutesText(day.plannedMinutes)} / ${minutesText(day.capacityMinutes)}`
      : minutesText(day.plannedMinutes);
    head.append(h('small', '', `${load}${day.plannedQuestions ? ` · ${day.plannedQuestions} soru` : ''}`));
    if (day.overCapacity) head.append(badge('Kapasite aşıldı', 'bad'));
    if (day.holiday) head.append(badge(day.holiday, 'warn'));
    column.append(head);
    const shadow = h('div', 'ak-coach-shadow');
    for (const lesson of day.lessons || []) {
      if (lesson.kind === 'break') continue;
      const line = h('div', `ak-coach-lesson${lesson.status === 'cancelled' ? ' cancelled' : ''}`);
      line.append(h('span', '', `${(lesson.startsAt || '').slice(11, 16)} ${lesson.subject?.shortName || lesson.subject?.name
        || LESSON_KIND[lesson.kind] || ''}`));
      if (lesson.status === 'cancelled') line.title = 'İptal';
      shadow.append(line);
    }
    if (day.studyHours?.length) {
      shadow.append(h('small', 'ak-muted', `Çalışma: ${day.studyHours.map((slot) => `${slot.from}–${slot.to}`).join(', ')}`));
    }
    if (shadow.childElementCount) column.append(shadow);
    const list = h('div', 'ak-coach-tasks');
    const tasks = [...(day.tasks || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const task of tasks) list.append(taskCard(task));
    if (!tasks.length) list.append(h('small', 'ak-muted', 'Görev yok'));
    column.append(list);
    if (app.can('admin_saveCoachWeek')) {
      column.append(button('+ Görev', { variant: 'ghost', onClick: () => editTask(null, day.date) }));
    }
    return column;
  }

  function taskCard(task) {
    const coach = isCoachTask(task);
    const card = h('div', `ak-coach-task${coach ? ' coach' : ' self'}${isDraft(task) ? ' draft' : ''}`);
    card.append(h('b', '', task.title || TASK_TYPES[task.type] || 'Görev'));
    const detail = taskDetail(task);
    card.append(h('small', '', [TASK_TYPES[task.type], minutesText(task.estimatedMinutes), detail].filter(Boolean).join(' · ')));
    const flags = h('div', 'ak-coach-flags');
    flags.append(coach ? badge('Koç görevi', 'info') : badge('Öğrencinin kendi işi', 'dim'));
    if (isDraft(task)) flags.append(badge('Taslak', 'warn'));
    if (task.status && task.status !== 'planned') flags.append(statusBadge(STATUS, task.status));
    if (task.coachTask?.priority === 'high') flags.append(badge('Öncelik yüksek', 'bad'));
    if (task.coachTask?.review?.status && task.coachTask.review.status !== 'none') {
      flags.append(badge({ pending: 'Kontrol bekliyor', approved: 'Onaylandı', redo: 'Tekrar istendi',
        commented: 'Yorumlandı' }[task.coachTask.review.status] || task.coachTask.review.status, 'dim'));
    }
    card.append(flags);
    if (coach && app.can('admin_saveCoachWeek')) {
      const tools = h('div', 'ak-row-actions');
      const days = (state.data?.days || []).map((day) => ({ value: day.date, label: `${SHORT_DAY[day.dayOfWeek] || ''} ${fmtDate(day.date).slice(0, 5)}` }));
      const mover = selectBox(days, task.date, (value) => {
        if (value && value !== task.date) save([{ op: 'move', clientKey: `mv-${task.id}`, taskId: task.id, date: value }], 'Görev taşındı.');
      }, { label: 'Güne taşı' });
      tools.append(mover.node,
        button('Düzenle', { variant: 'ghost', onClick: () => editTask(task) }),
        button('Sil', { variant: 'ghost', onClick: () => removeTask(task) }));
      card.append(tools);
    }
    return card;
  }

  // --- yazma

  async function save(changes, success) {
    if (!state.data || state.busy) return null;
    state.busy = true;
    try {
      const result = await app.api.put(`/v1/admin/students/${state.student.id}/coach-week`, {
        weekStart: state.data.weekStart, expectedVersion: state.data.version, ops: weekOps(changes) });
      const outcome = saveSummary(result);
      if (result.week) render(result.week);
      if (outcome.errors.length) {
        app.toast(`${outcome.errors.length} değişiklik kaydedilemedi (${outcome.errors.map((item) => item.errorCode).join(', ')}).`, 'bad');
      } else if (outcome.warnings.length) {
        app.toast(`${success} Uyarı: ${outcome.warnings.join(', ')}.`, 'warn');
      } else if (success) {
        app.toast(success, 'good');
      }
      return result;
    } catch (failure) {
      if (failure instanceof AkademiError && failure.code === 'version_conflict') {
        app.toast('Hafta bu arada başka biri tarafından değişti; güncel hali yüklendi. Değişikliği yeniden yapın.', 'bad');
        load();
      } else {
        app.toast(problem(failure), 'bad');
      }
      return null;
    } finally {
      state.busy = false;
    }
  }

  async function removeTask(task) {
    if (!await confirmAction(app, { title: 'Görevi sil', description: `"${task.title || TASK_TYPES[task.type]}" haftadan kalkar.`
      + (isDraft(task) ? '' : ' Öğrenci yayımlanmış programda değişikliği görür.'), confirmLabel: 'Sil', danger: true })) return;
    await save([{ op: 'delete', clientKey: `rm-${task.id}`, taskId: task.id }], 'Görev silindi.');
  }

  async function editTask(task, date = null) {
    if (!state.data) return;
    const result = await taskDialog(app, { studentId: state.student.id, week: state.data, task, date });
    if (!result) return;
    const clientKey = task ? `up-${task.id}` : `new-${Date.now()}`;
    await save([task ? { op: 'update', clientKey, taskId: task.id, task: result }
      : { op: 'create', clientKey, task: result }], task ? 'Görev güncellendi.' : 'Görev taslak olarak eklendi.');
  }

  async function publishWeek() {
    const data = state.data;
    if (!data) return;
    const form = await formDialog(app, {
      title: `Haftayı yayımla — ${weekLabel(data.weekStart)}`,
      description: `${data.draftCount || 0} taslak görev öğrenciye açılır.`,
      fields: [{ key: 'notify', label: 'Öğrenciye bildirim gönder', type: 'checkbox' }],
      value: { notify: true },
      submitLabel: 'Yayımla',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/students/${state.student.id}/coach-week/publish`,
        { weekStart: data.weekStart, notify: Boolean(draft.notify) }, { key }),
    });
    if (!form) return;
    app.toast('Hafta yayımlandı.', 'good');
    render(form);
  }

  async function carryOver() {
    const from = addDaysIso(state.week, -7);
    const form = await formDialog(app, {
      title: 'Geçen haftadan devret',
      description: `${weekLabel(from)} haftasında bitmeyen koç görevleri bu haftaya taşınır.`,
      fields: [
        { key: 'include', label: 'Hangi görevler', type: 'custom', render: (draft, set) => multiChips([
          { value: 'planned', label: 'Yapılmayan (planlı)' }, { value: 'partial', label: 'Kısmen yapılan' },
          { value: 'skipped', label: 'Atlanan' }], draft.include || [], (value) => set(value)).node },
        { key: 'asDraft', label: 'Taslak olarak taşı (yayımlayınca görünür)', type: 'checkbox' },
      ],
      value: { include: ['planned', 'partial'], asDraft: true },
      submitLabel: 'Devret',
      onSubmit: (draft, { key }) => {
        if (!draft.include?.length) throw new Error('En az bir görev türü seçin.');
        return app.api.post(`/v1/admin/students/${state.student.id}/coach-week/carry-over`,
          { fromWeekStart: from, include: draft.include, asDraft: Boolean(draft.asDraft) }, { key });
      },
    });
    if (!form) return;
    const unplaced = form.unplacedTaskIds?.length || 0;
    app.toast(`${form.movedCount} görev devredildi${unplaced ? `; ${unplaced} görev yer bulunamadığı için taşınmadı` : ''}.`,
      unplaced ? 'warn' : 'good');
    if (form.week) render(form.week);
  }

  async function copyWeek() {
    const source = { studentId: state.student.id, weekStart: state.data.weekStart };
    const targets = new Map();
    const run = (dryRun, extra, key) => app.api.post('/v1/admin/coaching/weeks/copy', {
      sourceStudentId: source.studentId, sourceWeekStart: source.weekStart,
      targets: [...targets.values()].map((item) => ({ studentId: item.id, weekStart: extra.weekStart,
        shiftDays: Number(extra.shiftDays) || 0 })),
      asDraft: Boolean(extra.asDraft), dryRun }, key ? { key } : {});
    const form = await formDialog(app, {
      title: 'Haftayı kopyala',
      description: `${state.student.displayName || 'Öğrenci'} · ${weekLabel(source.weekStart)} haftasının koç görevleri seçilen `
        + 'öğrencilere / haftaya kopyalanır. Önce deneme yapılır.',
      wide: true,
      fields: [
        { key: 'students', label: 'Hedef öğrenciler (aynı öğrenci de seçilebilir)', type: 'custom', render: () => {
          const box = h('div', 'ak-stack');
          const chips = h('div', 'ak-chips');
          const paint = () => {
            chips.replaceChildren();
            for (const item of targets.values()) {
              const chip = h('button', 'kit-chip on', `${item.displayName || item.id} ✕`);
              chip.type = 'button';
              chip.addEventListener('click', () => { targets.delete(item.id); paint(); });
              chips.append(chip);
            }
            if (!targets.size) chips.append(h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
          };
          const pick = studentPicker(app, { onPick: (student) => { targets.set(student.id, student); paint(); } });
          const self = button('Bu öğrenci', { variant: 'ghost', onClick: () => { targets.set(state.student.id, state.student); paint(); } });
          paint();
          box.append(toolbar(pick.node, self), chips);
          return box;
        } },
        { key: 'weekStart', label: 'Hedef hafta (pazartesi)', type: 'date', required: true },
        { key: 'shiftDays', label: 'Gün kaydırma', type: 'number', min: -6, max: 6 },
        { key: 'asDraft', label: 'Taslak olarak kopyala', type: 'checkbox' },
      ],
      value: { weekStart: addDaysIso(source.weekStart, 7), shiftDays: 0, asDraft: true },
      submitLabel: 'Dene',
      onSubmit: async (draft) => {
        if (!targets.size) throw new Error('Hedef öğrenci seçin.');
        if (weekStart(draft.weekStart) !== draft.weekStart) throw new Error('Hedef hafta pazartesi olmalı.');
        const preview = await run(true, draft);
        return { draft, preview };
      },
    });
    if (!form) return;
    const lines = (form.preview.results || []).map((row) => {
      const name = targets.get(row.studentId)?.displayName || row.studentId;
      const conflicts = row.conflicts?.length ? ` · çakışma: ${row.conflicts.join(', ')}` : '';
      return `${name} · ${weekLabel(row.weekStart)} · ${COPY_STATUS[row.status]?.[0] || row.status} · ${row.taskCount} görev${conflicts}`;
    });
    const willCopy = (form.preview.results || []).some((row) => row.status === 'would_copy');
    if (!willCopy) {
      app.toast('Kopyalanacak görev yok (hedeflerin hepsi atlandı).', 'warn');
      return;
    }
    if (!await confirmAction(app, { title: 'Kopyalamayı onayla', description: lines.join('\n'), confirmLabel: 'Kopyala' })) return;
    const done = await runAction(app, () => run(false, form.draft, `kopya-${source.studentId}-${source.weekStart}-${Date.now()}`),
      { success: 'Hafta kopyalandı.' });
    if (done) load();
  }

  async function weekPdf() {
    await coachingJob(app, () => app.api.post(`/v1/admin/students/${state.student.id}/coach-week/pdf`,
      { weekStart: state.data.weekStart }), { name: `haftalik-program-${state.data.weekStart}`,
      label: 'Haftalık program PDF\'i hazırlanıyor…' });
  }

  paintIdle();
  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.student?.id && payload.student.id !== state.student?.id) { started = true; choose(payload.student); return; }
      if (!started) { started = true; if (state.student) load(); }
    },
    destroy() { picker.destroy?.(); },
  };
}

// --------------------------------------------------------------- görev formu

/**
 * Görev oluşturucu. Dönüş: `PlanAssignmentTaskInput` (koç alanlarıyla) ya da
 * vazgeçildiyse null. Yeni görev taslaktır; düzenlemede taslak durumu korunur.
 */
export async function taskDialog(app, { studentId, week, task = null, date = null }) {
  const context = app.can('admin_getTaskBuilderContext')
    ? await app.api.get(`/v1/admin/students/${studentId}/task-builder`).catch(() => null) : null;
  const shelf = context?.shelf || [];
  const initial = task ? taskForm(task) : {
    date: date || week.days.find((day) => day.date >= todayIst(0))?.date || week.weekStart, type: 'question_solving',
    estimatedMinutes: 40, title: '', subjectId: '', topicId: '', resourceId: '', sectionIds: [], pageFrom: '', pageTo: '',
    targetQuestions: '', note: '', instructions: '', questionFrom: '', questionTo: '', priority: 'normal', proofPhoto: 'off',
    answersRequired: true, minAccuracyPercent: '', videos: [], attachmentFileIds: [],
  };
  const extra = { videos: initial.videos || [], attachmentFileIds: initial.attachmentFileIds || [],
    sectionIds: initial.sectionIds || [], resourceId: initial.resourceId || '', addToShelf: false };
  let topic = null;

  const resourceField = () => {
    const box = h('div', 'ak-stack');
    const sections = h('div', 'ak-stack');
    const options = [{ value: '', label: shelf.length ? 'Kaynak yok' : 'Rafta kaynak yok' },
      ...shelf.map((item) => ({ value: item.resourceId, label: `${item.title} · ${pct(item.progressPercent)}` }))];
    if (extra.resourceId && !shelf.some((item) => item.resourceId === extra.resourceId)) {
      options.push({ value: extra.resourceId, label: initial.resourceTitle || 'Kayıtlı kaynak' });
    }
    const loadSections = async (resourceId, preselect = []) => {
      sections.replaceChildren();
      if (!resourceId || !app.can('admin_getStudentResourceSections')) return;
      sections.append(h('small', 'ak-muted', 'Bölümler yükleniyor…'));
      try {
        const data = await app.api.get(`/v1/admin/students/${studentId}/resources/${resourceId}/sections`);
        const items = (data?.sections || []).filter((item) => item.kind !== 'unit');
        sections.replaceChildren();
        if (!data.onShelf) {
          const label = h('label', 'ak-inline');
          const check = h('input', 'kit-check');
          check.type = 'checkbox';
          check.addEventListener('change', () => { extra.addToShelf = check.checked; });
          label.append(check, h('span', '', 'Kaynak öğrencinin rafında değil — rafa da ekle'));
          sections.append(label);
        }
        if (!items.length) { sections.append(h('small', 'ak-muted', 'Bu kaynağın içindekiler tanımlı değil.')); return; }
        extra.sectionIds = preselect.filter((id) => items.some((item) => item.id === id));
        sections.append(multiChips(items.map((item) => ({ value: item.id, label: `${item.done ? '✓ ' : ''}${item.title}${item.pageFrom
          ? ` (s. ${item.pageFrom}${item.pageTo && item.pageTo !== item.pageFrom ? `–${item.pageTo}` : ''})` : ''}` })),
        extra.sectionIds, (value) => { extra.sectionIds = value; }).node,
        h('small', 'ak-muted', 'Bölüm seçilince sayfa, soru aralığı ve konu sunucuda kendiliğinden dolar.'));
      } catch (failure) {
        sections.replaceChildren(alertBox(problem(failure, 'Bölümler okunamadı.'), 'bad'));
      }
    };
    const select = selectBox(options, extra.resourceId, (value) => {
      extra.resourceId = value;
      const next = shelf.find((item) => item.resourceId === value)?.nextSection;
      loadSections(value, next ? [next.id] : []);
    }, { label: 'Kaynak' });
    box.append(select.node, sections);
    if (extra.resourceId) loadSections(extra.resourceId, extra.sectionIds);
    return box;
  };

  const videosField = () => {
    const box = h('div', 'ak-stack');
    const list = h('div', 'ak-stack');
    const paint = () => {
      list.replaceChildren();
      if (!extra.videos.length) list.append(h('small', 'ak-muted', 'Video yok.'));
      extra.videos.forEach((video, index) => {
        const line = h('div', 'ak-coach-action');
        line.append(videoCard(video.video || { title: video.videoId || 'Kütüphane öğesi' }),
          h('small', 'ak-muted', [video.startSeconds || video.endSeconds ? 'kesit' : '',
            video.checkpoints?.length ? `${video.checkpoints.length} durak sorusu` : ''].filter(Boolean).join(' · ')),
          button('Çıkar', { variant: 'ghost', onClick: () => { extra.videos.splice(index, 1); paint(); } }));
        list.append(line);
      });
    };
    const addVideo = button('Video ekle', { variant: 'ghost', onClick: async () => {
      const picked = await videoDialog(app, { max: 10 - extra.videos.length });
      if (picked?.length) { extra.videos.push(...picked); paint(); }
    } });
    addVideo.disabled = !(app.can('admin_resolveVideoLink') || app.can('admin_listCoachLibrary'));
    paint();
    box.append(list, addVideo);
    return box;
  };

  const filesField = () => {
    const box = h('div', 'ak-stack');
    const status = h('small', 'ak-muted', extra.attachmentFileIds.length ? `${extra.attachmentFileIds.length} ek` : '');
    if (!app.can('admin_uploadCoachingFile')) {
      box.append(h('small', 'ak-muted', 'Ek yükleme yetkiniz yok.'));
      return box;
    }
    const drop = fileDrop({ accept: ['pdf', 'png', 'jpg', 'jpeg', 'webp'], maxMb: 10, label: 'Foto ya da PDF ekleyin (en çok 3)',
      onFile: async (file) => {
        if (extra.attachmentFileIds.length >= 3) { status.textContent = 'En çok 3 ek eklenir.'; return; }
        status.textContent = 'Yükleniyor…';
        try {
          const result = await app.api.call('POST', '/v1/admin/coaching/files', {
            upload: { file, field: 'file', fields: { kind: 'coach_attachment' } } });
          if (result.data?.id) extra.attachmentFileIds.push(result.data.id);
          status.textContent = `${extra.attachmentFileIds.length} ek yüklendi.`;
          drop.clear();
        } catch (failure) {
          status.textContent = problem(failure, 'Dosya yüklenemedi.');
        }
      } });
    box.append(drop.node, status);
    return box;
  };

  const hints = [];
  if (context?.weakTopics?.length) {
    hints.push(`Zayıf konular: ${context.weakTopics.slice(0, 5).map((item) => `${item.topic.name}${item.accuracyPercent !== null
      && item.accuracyPercent !== undefined ? ` (${pct(item.accuracyPercent)})` : ''}`).join(', ')}`);
  }
  if (context?.dueReviews?.length) hints.push(`Tekrar zamanı gelen: ${context.dueReviews.slice(0, 5).map((item) => item.name).join(', ')}`);

  const days = week.days.map((day) => ({ value: day.date, label: `${DAY_LABEL[day.dayOfWeek] || ''} ${fmtDate(day.date)}${day.holiday
    ? ' (tatil)' : ''}` }));
  return formDialog(app, {
    title: task ? 'Koç görevini düzenle' : 'Koç görevi ekle',
    description: [task && !isDraft(task) ? 'Yayımlanmış görev: değişiklik öğrenciye bildirilir.'
      : 'Yeni görev taslaktır; "Yayımla"ya dek öğrenci görmez.', ...hints].join(' · '),
    wide: true,
    fields: [
      { key: 'date', label: 'Gün', type: 'select', required: true, options: days },
      { key: 'type', label: 'Tür', type: 'select', required: true, options: optionsOf(TASK_TYPES) },
      { key: 'estimatedMinutes', label: 'Süre (dk)', type: 'number', required: true, min: 5, max: 600 },
      { key: 'title', label: 'Başlık (boşsa türden)', maxLength: 120 },
      { key: 'topic', label: 'Ders ve konu', type: 'custom', render: () => {
        topic = topicPicker(app, { subjectId: initial.subjectId, topicId: initial.topicId, topicName: initial.topicName });
        return topic.node;
      } },
      { key: 'resource', label: 'Kaynak ve bölüm (raf)', type: 'custom', render: resourceField },
      { key: 'pageFrom', label: 'Sayfa (ilk)', type: 'number', min: 1 },
      { key: 'pageTo', label: 'Sayfa (son)', type: 'number', min: 1 },
      { key: 'questionFrom', label: 'Soru (ilk)', type: 'number', min: 1 },
      { key: 'questionTo', label: 'Soru (son)', type: 'number', min: 1 },
      { key: 'targetQuestions', label: 'Hedef soru sayısı', type: 'number', min: 1 },
      { key: 'instructions', label: 'Talimat (öğrenci görür)', type: 'textarea', maxLength: 2000, wide: true },
      { key: 'priority', label: 'Öncelik', type: 'select', options: optionsOf(PRIORITY) },
      { key: 'proofPhoto', label: 'Fotoğraf kanıtı', type: 'select', options: optionsOf(PROOF_PHOTO) },
      { key: 'answersRequired', label: 'Doğru / yanlış / boş zorunlu', type: 'checkbox' },
      { key: 'minAccuracyPercent', label: 'Başarı ölçütü: doğruluk % (altıysa tekrar önerilir)', type: 'number', min: 1, max: 100 },
      { key: 'videos', label: 'Videolar', type: 'custom', render: videosField },
      { key: 'attachments', label: 'Ekler', type: 'custom', render: filesField },
      { key: 'note', label: 'Not', type: 'textarea', maxLength: 500, wide: true },
    ],
    value: initial,
    submitLabel: task ? 'Kaydet' : 'Ekle',
    onSubmit: async (draft) => {
      const picked = topic?.value() || {};
      const built = taskInput({ ...draft, subjectId: picked.subjectId || '', topicId: picked.topicId || '',
        resourceId: extra.resourceId, sectionIds: extra.sectionIds, videos: extra.videos,
        attachmentFileIds: extra.attachmentFileIds, addToShelf: extra.addToShelf },
      { draft: task ? isDraft(task) : true });
      if (built.error) throw new Error(built.error);
      return built.task;
    },
  });
}

// ---------------------------------------------------------- şablonla atama

/** Kurum plan şablonunu sınıf / grup / öğrencilere ata: önizleme → uygula (arka plan işi). */
export async function templateAssignDialog(app, { onDone } = {}) {
  let templates = [];
  try {
    templates = (await app.api.getCached('/v1/admin/plan/templates', { limit: 100 }))?.items || [];
  } catch (failure) {
    app.toast(problem(failure, 'Şablonlar okunamadı.'), 'bad');
    return null;
  }
  if (!templates.length) {
    app.toast('Kurum plan şablonu yok: Dersler ve Program → Şablonlar ekranından ekleyin.', 'warn');
    return null;
  }
  const [classes, groups] = await loadTargets(app);
  let target = { classGroupIds: [], studentGroupIds: [], studentIds: [] };
  const body = (draft) => {
    if (!draft.templateId) throw new Error('Şablon seçin.');
    if (!target.classGroupIds.length && !target.studentGroupIds.length && !target.studentIds.length) {
      throw new Error('Sınıf, grup ya da öğrenci seçin.');
    }
    return { templateId: draft.templateId, scope: 'institution', target, startDate: draft.startDate,
      holidayPolicy: draft.holidayPolicy || 'shift_next', asDraft: Boolean(draft.asDraft), notify: Boolean(draft.notify) };
  };
  const form = await formDialog(app, {
    title: 'Şablonla toplu ata',
    description: 'Kurum plan şablonunun görevleri seçilen öğrencilerin haftasına koç görevi olarak yazılır. Önce önizleme '
      + 'gösterilir (en çok 200 öğrenci).',
    wide: true,
    fields: [
      { key: 'templateId', label: 'Şablon', type: 'select', required: true,
        options: [{ value: '', label: 'Şablon seçin' }, ...templates.map((item) => ({ value: item.id,
          label: `${item.name}${item.durationDays ? ` · ${item.durationDays} gün` : ''}${item.taskCount ? ` · ${item.taskCount} görev` : ''}` }))] },
      { key: 'target', label: 'Kime', type: 'custom', render: () => targetsField(app, { classes, groups,
        onChange: (value) => { target = value; } }).node },
      { key: 'startDate', label: 'Başlangıç', type: 'date', required: true },
      { key: 'holidayPolicy', label: 'Tatile denk gelen görev', type: 'select',
        options: [{ value: 'shift_next', label: 'Sonraki güne kaydır' }, { value: 'skip', label: 'Atla' }] },
      { key: 'asDraft', label: 'Taslak olarak yaz (koç yayımlar)', type: 'checkbox' },
      { key: 'notify', label: 'Öğrencilere bildirim', type: 'checkbox' },
    ],
    value: { templateId: '', startDate: weekStart(todayIst(7)), holidayPolicy: 'shift_next', asDraft: true, notify: true },
    submitLabel: 'Önizle',
    onSubmit: async (draft, { key }) => {
      const request = body(draft);
      const preview = await app.api.post('/v1/admin/coaching/template-assignments/preview', request, { key });
      return { request, preview };
    },
  });
  if (!form) return null;
  return new Promise((resolve) => {
    const view = openDrawer(app, { title: 'Şablonla atama — önizleme', subtitle: `${form.preview.students?.length || 0} öğrenci`,
      onClose: () => resolve(null) });
    const parts = [kpiRow([
      { label: 'Öğrenci', value: String(form.preview.students?.length || 0) },
      { label: 'Görev', value: String(form.preview.taskCount ?? 0) },
      { label: 'Çözülemeyen satır', value: String(form.preview.unresolvedCount ?? 0), tone: form.preview.unresolvedCount ? 'warn' : '' },
    ])];
    if (form.preview.unresolvedCount) {
      parts.push(hintBox('Çözülemeyen satır o öğrencide atlanır (ör. rafta uygun kaynak yok). Diğer görevler yazılır.'));
    }
    const table = staticList({
      load: async () => form.preview.students || [],
      columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.2fr)', cell: (row) => studentCell(row.student) },
        { key: 'tasks', label: 'Görevler', width: 'minmax(0, 2fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', `${row.tasks?.length || 0} görev · ${minutesText((row.tasks || [])
            .reduce((total, item) => total + (item.estimatedMinutes || 0), 0))}`),
          h('small', '', (row.tasks || []).slice(0, 3).map((item) => `${fmtDate(item.date).slice(0, 5)} ${item.title}`).join(' · ')));
          return cell;
        } },
        { key: 'issues', label: 'Uyarı', width: 'minmax(0, 1.2fr)', cell: (row) => [
          ...(row.unresolved || []).map((item) => `${item.rowIndex + 1}. satır: ${UNRESOLVED[item.reason] || item.reason}`),
          ...(row.conflicts || []).map((item) => `çakışma ${item}`)].join(' · ') || '—' },
      ],
      empty: { title: 'Hedefte öğrenci yok', text: 'Koçlu öğrenci seçtiğinizden emin olun.' },
    });
    table.reload();
    parts.push(table.node);
    const apply = actionButton(app, 'admin_applyTemplateAssignment', 'Uygula', { variant: 'primary', onClick: async () => {
      apply.disabled = true;
      const job = await coachingJob(app, () => app.api.post('/v1/admin/coaching/template-assignments', form.request),
        { download: false, label: 'Şablon atanıyor…' });
      if (job) {
        app.toast(`${job.result?.taskCount ?? 0} görev yazıldı${job.result?.unresolvedRows ? `, ${job.result.unresolvedRows} satır atlandı` : ''}.`,
          'good');
        view.close();
        onDone?.();
        resolve(job);
      } else {
        apply.disabled = false;
      }
    } });
    parts.push(toolbar(button('Vazgeç', { variant: 'ghost', onClick: () => view.close() }), apply));
    view.body.replaceChildren(...parts);
  });
}

