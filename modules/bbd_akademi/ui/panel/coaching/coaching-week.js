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
// (`admin_requestCoachWeekPdf`, arka plan işi). "Şablondan ekle" (tek öğrenci) ve "Bu haftayı şablon olarak
// kaydet" (`admin_getCoachWeekTemplateDraft` → kurum plan şablonu; Akademi README §14 madde 115). Şablonla toplu atama
// (`admin_previewTemplateAssignment` → `admin_applyTemplateAssignment`):
// kurum plan şablonu → sınıf / grup / öğrenci, önizlemeli.

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { badge, emptyState, hintBox, kpiRow, skeletonRows } from '../../../ui-kit/layout.js';
import { AkademiError, problem } from '../api.js';
import {
  DAY_LABEL, WEEKDAYS, actionButton, confirmAction, errorBox, fmtDate, formDialog, multiChips, openDrawer,
  runAction, selectBox, staticList, statusBadge, studentPicker, todayIst, toolbar, weekStart,
} from '../common.js';
import { loadTargets, targetsField } from '../plan-tasks.js';
import {
  COPY_STATUS, TASK_TYPES, UNRESOLVED, addDaysIso, isCoachTask, isDraft, minutesText, saveSummary, taskDetail, weekLabel,
  weekOps,
} from './coaching-logic.js';
import { savedLine, taskEditor } from '../shared/task-form.js';
import {
  SAVE_ERRORS, coachTaskBody, emptyForm, formFromCoachTask, nextForm, saveErrorText, serverErrors,
} from '../shared/task-form-logic.js';
import { coachingJob, setEnabled, studentCell } from './coaching-shared.js';
import { templateStart, weekTemplateBody } from '../shared/text-templates-logic.js';

const STATUS = { planned: ['Planlandı', 'dim'], done: ['Bitti', 'good'], partial: ['Kısmen', 'warn'],
  skipped: ['Yapılmadı', 'bad'], moved: ['Taşındı', 'dim'] };
const SHORT_DAY = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));
const LESSON_KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik', break: 'Ara' };

export function tab(app) {
  loadStyles(import.meta.url, '../shared/task-form.css');   // görev penceresi ilk açılışta stilsiz görünmesin
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
  // Akademi README §14 madde 115: tek öğrencinin haftasına şablondan görev ekle / bu haftayı şablon olarak kaydet.
  const fromTemplate = actionButton(app, 'admin_previewTemplateAssignment', 'Şablondan ekle', { variant: 'ghost',
    onClick: () => templateAssignDialog(app, { onDone: () => load(), student: state.student,
      startDate: templateStart(state.week, todayIst(0)) }) });
  const saveAsTemplate = actionButton(app, 'admin_createStudyPlanTemplate', 'Bu haftayı şablon olarak kaydet', {
    variant: 'ghost', onClick: () => saveWeekTemplate(app, state.student, state.week) });
  const studentActions = [add, publish, carry, copy, pdf, fromTemplate, saveAsTemplate];
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
    for (const control of [add, carry, copy, pdf, fromTemplate, saveAsTemplate]) setEnabled(control, true);
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
    await taskDialog(app, { studentId: state.student.id, week: state.data, task, date, persist,
      studentName: state.student.displayName || state.data.student?.displayName || '' });
  }

  /**
   * Görev penceresinin kaydı: tek değişiklik, iyimser kilit. Sürüm çakışmasında hafta
   * sessizce yenilenir ve AYNI değişiklik bir kez daha denenir (yaratma/güncelleme
   * içerikle gelir; ilk deneme hiçbir şey yazmadı). Görev hatası AkademiError olarak fırlar.
   */
  async function persist(change) {
    const send = () => app.api.put(`/v1/admin/students/${state.student.id}/coach-week`, {
      weekStart: state.data.weekStart, expectedVersion: state.data.version, ops: weekOps([change]) });
    let result;
    try {
      result = await send();
    } catch (failure) {
      if (!(failure instanceof AkademiError && failure.code === 'version_conflict')) throw failure;
      render(await app.api.get(`/v1/admin/students/${state.student.id}/coach-week`, { week: state.data.weekStart }));
      result = await send();
    }
    const outcome = saveSummary(result);
    if (result.week) render(result.week);
    if (outcome.errors.length) {
      const code = outcome.errors[0].errorCode;
      throw new AkademiError({ code, message: saveErrorText(code) });
    }
    const done = change.op === 'update' ? 'Görev güncellendi.' : 'Görev taslak olarak eklendi.';
    app.toast(outcome.warnings.length ? `${done} Uyarı: ${outcome.warnings.join(', ')}.` : done,
      outcome.warnings.length ? 'warn' : 'good');
    return { warnings: outcome.warnings, week: result.week };
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
 * Koç görevi penceresi (tek bileşen: `task-form.js`). Kaydetme PENCERE AÇIKKEN yapılır
 * (`persist`): sunucu reddederse (eksik alan, sürüm çakışması, raf) girilen her şey
 * yerinde kalır, hata ilgili alanın altına yazılır. "Kaydet ve yeni ekle" pencereyi
 * kapatmadan sıradaki görevi açar (gün, tür, süre, ders ve kanıt kuralları kalır).
 *
 * @param persist(change) → Promise<{warnings: string[], week}>; hata AkademiError fırlatır.
 * @returns {Promise<number>} kaydedilen görev sayısı (vazgeçildiyse 0)
 */
export async function taskDialog(app, { studentId, week, task = null, date = null, persist, studentName = '', seed = null }) {
  const builder = app.can('admin_getTaskBuilderContext')
    ? await app.api.get(`/v1/admin/students/${studentId}/task-builder`).catch(() => null) : null;
  const firstDay = () => date || week.days.find((day) => day.date >= todayIst(0) && !day.holiday)?.date
    || week.days.find((day) => day.date >= todayIst(0))?.date || week.weekStart;
  const remembered = lastCoachForm.get(studentId) || {};
  const initial = task ? formFromCoachTask(task) : emptyForm({ date: firstDay(), type: remembered.type || 'question_solving',
    estimatedMinutes: remembered.estimatedMinutes || 40, subjectId: remembered.subjectId || '',
    subjectName: remembered.subjectName || '', priority: remembered.priority || 'normal',
    proofPhoto: remembered.proofPhoto || 'off', answersRequired: remembered.answersRequired !== false, ...(seed || {}) });
  const draft = task ? isDraft(task) : true;
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog wide ak-tf-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    const head = h('div', 'ak-tf-head');
    head.append(h('h3', 'kit-dialog-title', task ? 'Koç görevini düzenle' : 'Koç görevi ekle'),
      h('p', 'kit-dialog-text', [studentName, task && !draft ? 'Yayımlanmış görev: değişiklik öğrenciye bildirilir.'
        : 'Yeni görev taslaktır; "Yayımla"ya dek öğrenci görmez.'].filter(Boolean).join(' · ')));
    const body = h('div', 'ak-tf-body');
    const savedSlot = h('div', 'ak-tf-saved-slot');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    const keys = h('small', 'ak-tf-keys', task ? 'Enter: kaydet · Esc: vazgeç'
      : 'Enter: kaydet · Shift+Enter: kaydet ve yeni ekle · Esc: vazgeç');
    const actions = h('div', 'kit-dialog-actions ak-tf-actions');
    let current = week;
    let saved = 0;
    let busy = false;
    let editor = null;
    const mount = (form, isNew) => {
      editor?.destroy();
      editor = taskEditor(app, { mode: 'coach', form, studentId, week: current, builder, isNew,
        onSubmit: (again) => submit(again && !task) });
      body.replaceChildren(savedSlot, editor.node);
    };
    const cancel = button(saved ? 'Kapat' : 'Vazgeç', { onClick: () => close() });
    const again = task ? null : button('Kaydet ve yeni ekle', { onClick: () => submit(true) });
    const go = button(task ? 'Kaydet' : 'Ekle', { variant: 'primary', onClick: () => submit(false) });
    actions.append(keys, h('span', 'kit-spacer'), cancel, ...(again ? [again] : []), go);
    card.append(head, body, error, actions);
    overlay.append(card);

    let release = () => {};
    const onKey = (event) => { if (event.key === 'Escape' && !busy) close(); };
    function close() {
      document.removeEventListener('keydown', onKey);
      editor?.destroy();
      overlay.remove();
      release();
      resolve(saved);
    }

    async function submit(andNew) {
      if (busy) return;
      error.textContent = '';
      const result = editor.check();
      if (!result.ok) {
        const count = Object.keys(result.errors).length;
        error.textContent = count > 1 ? `${count} alanı düzeltin (işaretli).` : 'İşaretli alanı düzeltin.';
        editor.focusFirst(result.first);
        return;
      }
      const form = editor.value();
      const built = coachTaskBody(form, { draft });
      if (built.error) { error.textContent = built.error; return; }
      busy = true;
      for (const control of [go, again].filter(Boolean)) control.disabled = true;
      go.textContent = 'Kaydediliyor…';
      try {
        const clientKey = task ? `up-${task.id}` : `new-${Date.now()}`;
        const out = await persist(task ? { op: 'update', clientKey, taskId: task.id, task: built.task }
          : { op: 'create', clientKey, task: built.task });
        saved += 1;
        cancel.textContent = 'Kapat';
        lastCoachForm.set(studentId, { type: form.type, estimatedMinutes: form.estimatedMinutes, subjectId: form.subjectId,
          subjectName: form.subjectName, priority: form.priority, proofPhoto: form.proofPhoto, answersRequired: form.answersRequired });
        const title = built.task.title || TASK_TYPES[form.type] || 'Görev';
        const when = `${DAY_LABEL[current.days.find((day) => day.date === form.date)?.dayOfWeek] || fmtDate(form.date)}`;
        if (out?.week) current = out.week;
        if (andNew) {
          mount(nextForm(form), true);
          savedSlot.replaceChildren(savedLine(`"${title}" ${when} gününe eklendi${out?.warnings?.length
            ? ` (uyarı: ${out.warnings.join(', ')})` : ''}. Sıradaki görevi girin.`));
          editor.focusFirst();
        } else {
          close();
        }
      } catch (failure) {
        const code = failure instanceof AkademiError ? failure.code : '';
        const map = serverErrors(code, failure?.details, editor.value());
        const { _form: formError, ...fieldErrors } = map;
        if (Object.keys(fieldErrors).length) editor.showErrors(fieldErrors);
        error.textContent = formError || (Object.keys(fieldErrors).length ? 'İşaretli alanları düzeltin; girdikleriniz duruyor.'
          : `${SAVE_ERRORS[code] || problem(failure)} Girdikleriniz duruyor.`);
      } finally {
        busy = false;
        for (const control of [go, again].filter(Boolean)) control.disabled = false;
        go.textContent = task ? 'Kaydet' : 'Ekle';
      }
    }

    mount(initial, !task);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay && !busy && !saved) close(); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    editor.focusFirst();
  });
}

/**
 * Haftalık programı açmadan görev ver (koçluk kartı, hata defteri): bu haftanın programı okunur,
 * görev penceresi aynı kurallarla açılır, kayıt aynı tek istekle gider (sürüm çakışmasında
 * hafta yenilenip bir kez daha denenir). `seed`: başlangıç alanları (ör. hata tekrarı + ders).
 * @returns {Promise<number>} kaydedilen görev sayısı
 */
export async function openCoachTask(app, student, { seed = null, week: weekIso = null } = {}) {
  loadStyles(import.meta.url, '../shared/task-form.css');
  const path = `/v1/admin/students/${student.id}/coach-week`;
  let data;
  try {
    data = await app.api.get(path, { week: weekIso || weekStart(todayIst(0)) });
  } catch (failure) {
    app.toast(problem(failure, 'Haftalık program okunamadı.'), 'bad');
    return 0;
  }
  const persist = async (change) => {
    const send = () => app.api.put(path, { weekStart: data.weekStart, expectedVersion: data.version, ops: weekOps([change]) });
    let result;
    try {
      result = await send();
    } catch (failure) {
      if (!(failure instanceof AkademiError && failure.code === 'version_conflict')) throw failure;
      data = await app.api.get(path, { week: data.weekStart });
      result = await send();
    }
    if (result.week) data = result.week;
    const outcome = saveSummary(result);
    if (outcome.errors.length) {
      const code = outcome.errors[0].errorCode;
      throw new AkademiError({ code, message: saveErrorText(code) });
    }
    app.toast(`Görev taslak olarak eklendi${outcome.warnings.length ? ` (uyarı: ${outcome.warnings.join(', ')})` : ''}. `
      + 'Öğrenci, haftalık programda "Yayımla"dan sonra görür.', outcome.warnings.length ? 'warn' : 'good');
    return { warnings: outcome.warnings, week: result.week };
  };
  return taskDialog(app, { studentId: student.id, week: data, persist, seed,
    studentName: student.displayName || data.student?.displayName || '' });
}

/** Öğrenci başına son kullanılan tür / süre / ders (oturum boyunca; akıllı varsayılan). */
const lastCoachForm = new Map();

// ---------------------------------------------------------- şablonla atama

/** Kurum plan şablonunu sınıf / grup / öğrencilere ata: önizleme → uygula (arka plan işi). */
export async function templateAssignDialog(app, { onDone, student = null, startDate = null } = {}) {
  let templates = [];
  try {
    templates = (await app.api.getCached('/v1/admin/plan/templates', { limit: 100 }))?.items || [];
  } catch (failure) {
    app.toast(problem(failure, 'Şablonlar okunamadı.'), 'bad');
    return null;
  }
  if (!templates.length) {
    app.toast('Kurum plan şablonu yok: Koçluk → Şablonlar → Program şablonları ekranından ekleyin (ya da bir haftayı '
      + '“Bu haftayı şablon olarak kaydet” ile şablon yapın).', 'warn');
    return null;
  }
  const [classes, groups] = student ? [[], []] : await loadTargets(app);
  let target = { classGroupIds: [], studentGroupIds: [], studentIds: student ? [student.id] : [] };
  const body = (draft) => {
    if (!draft.templateId) throw new Error('Şablon seçin.');
    if (!target.classGroupIds.length && !target.studentGroupIds.length && !target.studentIds.length) {
      throw new Error('Sınıf, grup ya da öğrenci seçin.');
    }
    return { templateId: draft.templateId, scope: 'institution', target, startDate: draft.startDate,
      holidayPolicy: draft.holidayPolicy || 'shift_next', asDraft: Boolean(draft.asDraft), notify: Boolean(draft.notify) };
  };
  const form = await formDialog(app, {
    title: student ? `Şablondan ekle — ${student.displayName || 'öğrenci'}` : 'Şablonla toplu ata',
    description: student
      ? 'Plan şablonunun görevleri öğrencinin programına koç görevi olarak yazılır (gün = başlangıç + şablondaki gün). '
        + 'Önce önizleme gösterilir.'
      : 'Kurum plan şablonunun görevleri seçilen öğrencilerin haftasına koç görevi olarak yazılır. Önce önizleme '
        + 'gösterilir (en çok 200 öğrenci).',
    wide: true,
    fields: [
      { key: 'templateId', label: 'Şablon', type: 'select', required: true,
        options: [{ value: '', label: 'Şablon seçin' }, ...templates.map((item) => ({ value: item.id,
          label: `${item.name}${item.durationDays ? ` · ${item.durationDays} gün` : ''}${item.taskCount ? ` · ${item.taskCount} görev` : ''}` }))] },
      student ? null : { key: 'target', label: 'Kime', type: 'custom', render: () => targetsField(app, { classes, groups,
        onChange: (value) => { target = value; } }).node },
      { key: 'startDate', label: 'Başlangıç', type: 'date', required: true },
      { key: 'holidayPolicy', label: 'Tatile denk gelen görev', type: 'select',
        options: [{ value: 'shift_next', label: 'Sonraki güne kaydır' }, { value: 'skip', label: 'Atla' }] },
      { key: 'asDraft', label: 'Taslak olarak yaz (koç yayımlar)', type: 'checkbox' },
      { key: 'notify', label: 'Öğrencilere bildirim', type: 'checkbox' },
    ],
    value: { templateId: '', startDate: startDate || weekStart(todayIst(7)), holidayPolicy: 'shift_next', asDraft: true,
      notify: !student },
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


// ---------------------------------------------------------- haftadan şablon (Akademi README §14 madde 115)

/** "Bu haftayı şablon olarak kaydet": haftanın koç görevleri → kurum plan şablonu (Koçluk → Şablonlar). */
export async function saveWeekTemplate(app, student, week) {
  if (!student?.id) return null;
  let draft;
  try {
    draft = await app.api.get(`/v1/admin/students/${student.id}/coach-week/template-draft`, { week });
  } catch (failure) {
    app.toast(problem(failure, 'Hafta okunamadı.'), 'bad');
    return null;
  }
  if (!draft?.tasks?.length) {
    app.toast('Bu haftada şablona alınacak koç görevi yok.', 'warn');
    return null;
  }
  const result = await formDialog(app, {
    title: 'Bu haftayı şablon olarak kaydet',
    description: `${draft.tasks.length} koç görevi şablona alınır (gün sırasıyla)`
      + `${draft.skippedCount ? `; ${draft.skippedCount} görev atlandı (ödev, deneme, video görevi)` : ''}. `
      + 'Şablon Koçluk → Şablonlar → Program şablonlarında görünür; “Şablondan ekle” ile başka öğrencilere yazılır.',
    fields: [
      { key: 'name', label: 'Şablon adı', required: true, maxLength: 80, wide: true },
      { key: 'description', label: 'Açıklama (isteğe bağlı)', type: 'textarea', maxLength: 500, wide: true },
    ],
    value: { name: draft.name, description: '' },
    submitLabel: 'Şablonu kaydet',
    onSubmit: (values, { key }) => {
      const built = weekTemplateBody({ ...draft, description: String(values.description || '').trim() || null }, values.name);
      if (built.error) throw new Error(built.error);
      return app.api.post('/v1/admin/plan/templates', built.body, { key });
    },
  });
  if (!result) return null;
  app.api.invalidate('/v1/admin/plan/templates');
  app.toast(`“${result.name}” şablonu kaydedildi.`, 'good');
  return result;
}
