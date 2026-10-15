// BBD Akademi — Çalışma planı yönetimi (F-A18; öğrenci tarafı F-S16).
//
// Dört sekme:
//   · Atamalar — öğrenciye / çalışma grubuna / sınıfa koç görevi. Hedefteki her
//     öğrenciye `source=coach`, `locked=true` görev açılır: öğrenci silemez,
//     gününü değiştiremez, yalnız erteleme ister. Sonradan sınıfa katılan
//     öğrenciye geriye dönük uygulanmaz (Akademi kuralı). Düzenleme yalnız
//     YAPILMAMIŞ görevlerin gününü / saatini / süresini / başlığını / hedef
//     sorusunu / notunu değiştirir; geri almak yapılmamış görevleri kaldırır,
//     tamamlanan ve kısmi görevler geçmişte kalır.
//   · Şablonlar — gün ofsetli görev listeleri; yalnız KM (plan-templates.js).
//   · Uyum tablosu — öğrenci × hafta plan uyumu %, ön hesaplı günlük
//     özetlerden (en çok 12 hafta). Hücre rengi tek başına anlam taşımaz:
//     her hücrede yüzde yazılıdır.
//   · Erteleme talepleri — koç görevi için öğrencinin talebi; onaylanırsa görev
//     onaylanan güne taşınır ve öğrenciye bildirim gider. Tatil gününe onay
//     verilemez (`date_is_holiday`).
//
// TATİL. Kurum takviminde tatil olan güne düşen görev, atamanın
// `holidayPolicy`'sine göre atlanır ya da sonraki güne kayar; karar Akademi'de.
//
// HIZ. Listeler imleçlidir (pagedList); hedef adları önbellekli sınıf/grup
// listesinden, ders adları önbellekli ders listesinden çözülür — satır başına
// istek atılmaz. Görev ayrıntısı (konu/kaynak adı) yükte yok; görev başlığı
// oluşturulurken seçimden kurulur (plan-tasks.js).
//
// Derin bağlantı: `{tab}`, `{planAssignmentId}`, `{studentId, studentName?}`
// (Atamalar ve Erteleme talepleri öğrenciye göre süzülür).

import { h, loadStyles } from '../../ui-kit/kit.js';
import { badge, card, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import {
  actionButton, addDays, confirmAction, empty, fmtDate, fmtDateTime, formDialog, kv, loader, num,
  openDrawer, optionsOf, pagedList, percent, reasonDialog, runAction, sectionHead, selectBox,
  statusBadge, studentPicker, todayIst, toolbar, weekStart,
} from './common.js';
import {
  CLOCK, HOLIDAY_POLICIES, TASK_TYPES, detailText, loadTargets, rangeText, subjectMap,
  targetText, targetsField, taskBody, taskListField, taskPatches, taskTitle, timeText, totalMinutes,
  weekdayOf, whenText,
} from './plan-tasks.js';
import { applyTemplate, templateNotice, templatesTab } from './plan-templates.js';

const POSTPONE_STATUS = {
  requested: ['Bekliyor', 'warn'], approved: ['Onaylandı', 'good'], declined: ['Reddedildi', 'dim'],
  cancelled: ['Öğrenci geri aldı', 'dim'],
};
const PERIODS = { '': 'Tüm tarihler', this_week: 'Bu hafta', next_week: 'Gelecek hafta',
  next_30: 'Önümüzdeki 30 gün', last_30: 'Son 30 gün' };
const SORTS = { name: 'Ada göre', adherence_asc: 'Uyumu düşük önce', adherence_desc: 'Uyumu yüksek önce' };

function periodRange(key) {
  const today = todayIst();
  const monday = weekStart(today);
  switch (key) {
    case 'this_week': return { from: monday, to: addDays(monday, 6) };
    case 'next_week': return { from: addDays(monday, 7), to: addDays(monday, 13) };
    case 'next_30': return { from: today, to: addDays(today, 29) };
    case 'last_30': return { from: addDays(today, -29), to: today };
    default: return {};
  }
}

const studentCell = (student) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', student?.displayName || '—'),
    h('small', '', [student?.externalRef, student?.classGroup?.name].filter(Boolean).join(' · ')));
  return cell;
};

/** Uyum yüzdesi → ton. Eşik %50, erken uyarının "plan uyumu %50 altı" kuralıyla aynı (F-A22). */
function heatTone(value) {
  if (value === null || value === undefined) return 'none';
  if (value >= 80) return 'good';
  if (value >= 50) return 'mid';
  return 'low';
}

function heatCell(value, title = '') {
  const node = h('span', `ak-plan-heat ${heatTone(value)}`,
    value === null || value === undefined ? '—' : percent(value, 0));
  if (title) node.title = title;
  return node;
}

/** Sürmekte olan hafta: değer yazılır ama ısı rengi verilmez (yarım hafta düşük görünür). */
function liveCell(value, title = '') {
  const node = h('span', 'ak-plan-heat none ak-plan-heat-live',
    value === null || value === undefined ? '—' : percent(value, 0));
  if (title) node.title = title;
  return node;
}

/** İlerleme: çubuk + "12/40" + yüzde. Çubuk süstür; sayı her zaman yazılı. */
function progressCell(progress) {
  const total = progress?.taskInstances || 0;
  const done = progress?.done || 0;
  const cell = h('div', 'ak-plan-progress');
  const bar = h('span', 'ak-plan-bar');
  const fill = h('span', 'ak-plan-bar-fill');
  fill.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
  bar.append(fill);
  cell.append(bar, h('span', 'ak-num', total ? `${num(done)}/${num(total)} · ${percent((done / total) * 100, 0)}` : '—'));
  cell.title = `Yapıldı ${num(done)} · kısmi ${num(progress?.partial || 0)} · atlandı ${num(progress?.skipped || 0)}`
    + ` · bekleyen ${num(progress?.planned || 0)}`;
  return cell;
}

/**
 * Öğrenci süzgeci: seçilince arama kutusu yerine "Öğrenci: Ad ✕" çipi.
 * `set(student, notify)`.
 */
function studentFilter(app, { value = null, onChange } = {}) {
  const node = h('div', 'ak-plan-student-filter');
  let current = value;
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => set(student, true) });
  const chip = h('button', 'kit-chip on');
  chip.type = 'button';
  chip.title = 'Öğrenci süzgecini kaldır';
  chip.addEventListener('click', () => set(null, true));
  node.append(picker.node, chip);
  function paint() {
    picker.node.hidden = Boolean(current);
    chip.hidden = !current;
    chip.textContent = current ? `Öğrenci: ${current.displayName || 'seçili öğrenci'} ✕` : '';
  }
  function set(next, notify = false) {
    current = next?.id ? { id: next.id, displayName: next.displayName || next.fullName || '' } : null;
    paint();
    if (notify) onChange?.(current);
  }
  paint();
  return { node, value: () => current, set, destroy: picker.destroy };
}

// =================================================================== bölüm

export function section(app) {
  loadStyles(import.meta.url, 'plan.css');
  const node = h('div', 'ak-stack ak-plan');
  let started = false;
  let student = null;
  let lookups = null;

  /** Sınıf/grup adları (önbellekli) — ilk gösterimde bir kez. */
  const ready = () => {
    if (!lookups) {
      lookups = loadTargets(app).then(([classes, groups]) => ({
        classes,
        groups,
        names: { classes: new Map(classes.map((item) => [item.id, item.name])),
          groups: new Map(groups.map((item) => [item.id, item.name])) },
      }));
    }
    return lookups;
  };

  const ctx = {
    ready,
    student: () => student,
    openAssignment: (id) => assignmentDrawer(app, id, ctx),
    /** Atama eklendi/değişti/silindi → liste yeniden. */
    changed: () => bodies.assignments?.reload(),
    onApplied: (assignment) => {
      bodies.assignments?.reload();
      assignmentDrawer(app, assignment.id, ctx);
    },
    /** Uyum tablosundan: öğrencinin atamalarına geç. */
    showStudent: (next) => {
      setStudent(next);
      tabs.select('assignments');
    },
  };

  const create = actionButton(app, 'admin_createPlanAssignment', 'Yeni atama', { variant: 'primary',
    onClick: async () => {
      const result = await createAssignment(app, ctx);
      if (result) ctx.onApplied(result);
    } });
  const fromTemplate = actionButton(app, 'admin_applyStudyPlanTemplate', 'Şablondan ata', {
    onClick: () => applyTemplate(app, null, ctx) });
  node.append(sectionHead('Çalışma planı', {
    subtitle: 'Öğrencilere, gruplara ve sınıflara koç görevi; şablonlar, plan uyumu ve erteleme talepleri.',
    actions: [fromTemplate, create],
  }));

  const bodies = {};
  const tabs = tabBar([
    { key: 'assignments', label: 'Atamalar' },
    { key: 'templates', label: 'Şablonlar' },
    { key: 'adherence', label: 'Uyum tablosu' },
    { key: 'postpone', label: 'Erteleme talepleri' },
  ], 'assignments', (key) => showTab(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  const builders = {
    assignments: () => assignmentsTab(app, ctx, (next) => setStudent(next)),
    templates: () => templatesTab(app, ctx),
    adherence: () => adherenceTab(app, ctx),
    postpone: () => postponeTab(app, ctx, tabs, (next) => setStudent(next)),
  };

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  /** Öğrenci süzgeci Atamalar ve Erteleme sekmelerinde ORTAKTIR. */
  function setStudent(next) {
    const id = next?.id || null;
    if ((student?.id || null) === id) return;
    student = id ? { id, displayName: next.displayName || '' } : null;
    for (const body of Object.values(bodies)) body.setStudent?.(student);
  }

  return {
    node,
    shown(payload) {
      if (!started) {
        started = true;
        if (payload?.studentId) {
          student = { id: payload.studentId, displayName: payload.studentName || payload.displayName || '' };
        }
        const first = payload?.tab && builders[payload.tab] ? payload.tab : 'assignments';
        if (first !== 'assignments') tabs.select(first, false);
        showTab(first);
      } else {
        if (payload?.studentId) {
          setStudent({ id: payload.studentId, displayName: payload.studentName || payload.displayName || '' });
        }
        if (payload?.tab && builders[payload.tab]) tabs.select(payload.tab);
      }
      if (payload?.planAssignmentId) ctx.openAssignment(payload.planAssignmentId);
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// ================================================================= atamalar

function assignmentsTab(app, ctx, onStudent) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', studentGroupId: '', period: '' };
  let names = { classes: new Map(), groups: new Map() };

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '', (value) => {
    filters.studentGroupId = value; reload();
  }, { label: 'Çalışma grubu' });
  const periodSelect = selectBox(optionsOf(PERIODS), '', (value) => { filters.period = value; reload(); },
    { label: 'Görev tarihi' });
  const student = studentFilter(app, { value: ctx.student(), onChange: (next) => onStudent(next) });
  node.append(toolbar(classSelect.node, groupSelect.node, periodSelect.node, student.node));

  const list = pagedList({
    app,
    path: '/v1/admin/plan/assignments',
    query: () => ({ classGroupId: filters.classGroupId, studentGroupId: filters.studentGroupId,
      studentId: student.value()?.id || '', ...periodRange(filters.period) }),
    columns: [
      { key: 'title', label: 'Atama', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const title = h('span', '', row.title || `${num((row.tasks || []).length)} görevlik atama`);
        if (row.templateId) title.append(' ', badge('Şablon', 'info'));
        cell.append(title, h('small', '', rangeText(row.tasks)));
        return cell;
      } },
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', targetText(row.target, names)), h('small', '', `${num(row.studentCount)} öğrenci`));
        return cell;
      } },
      { key: 'tasks', label: 'Görev', width: '110px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', `${num((row.tasks || []).length)} görev`),
          h('small', '', `${num(totalMinutes(row.tasks))} dk / öğrenci`));
        return cell;
      } },
      { key: 'progress', label: 'İlerleme', width: 'minmax(150px, 1.2fr)', cell: (row) => progressCell(row.progress) },
      { key: 'by', label: 'Oluşturan', width: 'minmax(0, 1.2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.createdBy || '—'), h('small', '', fmtDateTime(row.createdAt)));
        return cell;
      } },
    ],
    onRow: (row) => ctx.openAssignment(row.id),
    empty: { title: 'Bu süzgece uyan plan ataması yok',
      text: 'Süzgeçleri temizleyin ya da “Yeni atama” ile koç görevi verin.' },
  });
  node.append(list.node);

  async function reload() {
    const loaded = await ctx.ready();
    names = loaded.names;
    if (classSelect.node.options.length <= 1) {
      classSelect.options([{ value: '', label: 'Tüm sınıflar' },
        ...loaded.classes.map((item) => ({ value: item.id, label: item.name }))]);
      groupSelect.options([{ value: '', label: 'Tüm gruplar' },
        ...loaded.groups.map((item) => ({ value: item.id, label: item.name }))]);
    }
    return list.reload();
  }

  return {
    node,
    reload,
    setStudent(next) { student.set(next); reload(); },
    destroy() { list.destroy(); student.destroy(); },
  };
}

/** Yeni atama penceresi. Sonuç oluşturulan atama (ya da null). */
async function createAssignment(app, ctx) {
  const { classes, groups } = await ctx.ready();
  const preset = ctx.student();
  let targets = null;
  let tasks = null;
  try {
    const result = await formDialog(app, {
      title: 'Yeni plan ataması',
      description: 'Hedefteki her öğrencinin planına “koç görevi” olarak düşer: öğrenci silemez ve gününü '
        + 'değiştiremez, yalnız erteleme isteyebilir. Sonradan sınıfa katılan öğrenciye uygulanmaz.',
      wide: true,
      value: { title: '', holidayPolicy: 'shift_next', notify: true },
      fields: [
        { key: 'title', label: 'Atama adı (isteğe bağlı)', maxLength: 120, wide: true,
          placeholder: 'Ekim 2. hafta — matematik yoğun' },
        { key: 'holidayPolicy', label: 'Tatile denk gelen görev', type: 'select',
          options: optionsOf(HOLIDAY_POLICIES), hint: 'Kurum takviminde tatil olan günler için.' },
        { key: 'notify', label: 'Öğrencilere bildirim gönder', type: 'checkbox' },
        { key: 'target', label: 'Kime', type: 'custom', required: true, render: (draft, set) => {
          targets = targetsField(app, { classes, groups, onChange: set,
            value: preset ? { students: [preset] } : {} });
          set(targets.value());
          return targets.node;
        }, validate: () => (targets?.count() ? null : 'En az bir sınıf, grup ya da öğrenci seçin.') },
        { key: 'tasks', label: 'Görevler', type: 'custom', required: true, render: (draft, set) => {
          tasks = taskListField(app, { mode: 'date', onChange: set });
          set(tasks.value());
          return tasks;
        }, validate: () => (tasks?.isEditing() ? 'Açık görev formunu listeye ekleyin ya da vazgeçin.' : null) },
      ],
      submitLabel: 'Atamayı oluştur',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/plan/assignments', {
        target: draft.target,
        title: String(draft.title || '').trim() || null,
        tasks: (draft.tasks || []).map((task) => taskBody(task, 'date')),
        holidayPolicy: draft.holidayPolicy || 'shift_next',
        notify: Boolean(draft.notify),
      }, { key }),
    });
    if (result) {
      app.toast(`Atama oluşturuldu: ${num(result.studentCount)} öğrenciye ${num((result.tasks || []).length)} görev.`,
        'good');
    }
    return result;
  } finally {
    targets?.destroy();
    tasks?.destroy();
  }
}

/** Atamayı düzenle: yalnız yapılmamış görevlerin gün/saat/süre/başlık/hedef/not alanları. */
async function editAssignment(app, item) {
  let tasks = null;
  const original = item.tasks || [];
  try {
    return await formDialog(app, {
      title: 'Atamayı düzenle',
      description: 'Değişiklik yalnız yapılmamış görevlere uygulanır; tamamlanan ve kısmi görevler olduğu gibi kalır. '
        + 'Tür ve içerik değişmez — gerekirse atamayı geri alıp yenisini açın.',
      wide: true,
      value: { title: item.title || '', notify: false },
      fields: [
        { key: 'title', label: 'Atama adı', maxLength: 120, wide: true },
        { key: 'notify', label: 'Öğrencilere değişiklik bildirimi gönder', type: 'checkbox' },
        { key: 'tasks', label: 'Görevler', type: 'custom', render: (draft, set) => {
          tasks = taskListField(app, { mode: 'patch', tasks: original, onChange: set });
          set(tasks.value());
          return tasks;
        }, validate: () => (tasks?.isEditing() ? 'Açık görev formunu kaydedin ya da vazgeçin.' : null) },
      ],
      submitLabel: 'Değişiklikleri kaydet',
      onSubmit: (draft) => {
        const body = {};
        const title = String(draft.title || '').trim() || null;
        if (title !== (item.title || null)) body.title = title;
        const changes = taskPatches(original, draft.tasks || []);
        if (changes.length) body.tasks = changes;
        if (!Object.keys(body).length) throw new AkademiError({ code: 'no_change', message: 'Değişiklik yok.' });
        body.notify = Boolean(draft.notify);
        return app.api.patch(`/v1/admin/plan/assignments/${item.id}`, body);
      },
    });
  } finally {
    tasks?.destroy();
  }
}

function assignmentDrawer(app, id, ctx) {
  const students = pagedList({
    app,
    path: `/v1/admin/plan/assignments/${id}/students`,
    limit: 50,
    dense: true,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.8fr)', cell: (row) => studentCell(row.student) },
      { key: 'planned', label: 'Bekleyen', width: '72px', align: 'num', cell: (row) => num(row.planned) },
      { key: 'done', label: 'Yapıldı', width: '64px', align: 'num', cell: (row) => num(row.done) },
      { key: 'partial', label: 'Kısmi', width: '56px', align: 'num', cell: (row) => num(row.partial) },
      { key: 'skipped', label: 'Atlandı', width: '64px', align: 'num', cell: (row) => num(row.skipped) },
      { key: 'moved', label: 'Taşındı', width: '64px', align: 'num', cell: (row) => num(row.moved),
        title: 'Ertelemeyle başka güne taşınan görevler; uyum hesabına girmez.' },
      { key: 'adherence', label: 'Uyum', width: '66px', cell: (row) => heatCell(row.adherencePercent) },
    ],
    empty: { title: 'Hedefte öğrenci yok', text: 'Sınıf ya da grup boş olabilir.' },
  });
  const panel = openDrawer(app, { title: 'Plan ataması', subtitle: 'Koç görevi — öğrenci yalnız erteleme isteyebilir',
    onClose: () => students.destroy() });
  panel.node.classList.add('ak-plan-drawer');
  const view = loader(() => Promise.all([app.api.get(`/v1/admin/plan/assignments/${id}`), ctx.ready(), subjectMap(app)]),
    ([item, loaded, subjects]) => render(item, loaded.names, subjects), { rows: 6, columns: 5 });
  panel.body.append(view.node);
  view.reload();
  students.reload();

  function render(item, names, subjects) {
    panel.setTitle(item.title || `${num((item.tasks || []).length)} görevlik atama`);
    const progress = item.progress || {};
    const tools = h('div', 'ak-inline');
    tools.append(
      actionButton(app, 'admin_updatePlanAssignment', 'Düzenle', { variant: 'primary', onClick: async () => {
        const next = await editAssignment(app, item);
        if (next) { app.toast('Atama güncellendi.', 'good'); view.reload(); ctx.changed(); }
      } }),
      actionButton(app, 'admin_deletePlanAssignment', 'Atamayı geri al', { variant: 'ghost', onClick: async () => {
        const yes = await confirmAction(app, { title: 'Atama geri alınsın mı?',
          description: 'Yapılmamış görevler öğrencilerin planından kalkar; tamamlanan ve kısmi görevler geçmişte kalır.',
          confirmLabel: 'Geri al', danger: true });
        if (!yes) return;
        const done = await runAction(app, async () => {
          await app.api.del(`/v1/admin/plan/assignments/${id}`);
          return true;
        }, { success: 'Atama geri alındı.' });
        if (done) { panel.close(); ctx.changed(); }
      } }),
    );
    const nodes = [tools];
    const notice = templateNotice(item.templateId);
    if (notice) nodes.push(notice);
    nodes.push(kpiRow([
      { label: 'Öğrenci', value: num(item.studentCount) },
      { label: 'Toplam görev', value: num(progress.taskInstances), title: 'Öğrenci × görev' },
      { label: 'Yapıldı', value: num(progress.done), tone: progress.done ? 'good' : '' },
      { label: 'Kısmi', value: num(progress.partial) },
      { label: 'Atlandı', value: num(progress.skipped), tone: progress.skipped ? 'bad' : '' },
      { label: 'Bekleyen', value: num(progress.planned) },
    ]));
    nodes.push(card('', kv([
      ['Hedef', targetText(item.target, names)],
      ['Tarih aralığı', rangeText(item.tasks)],
      ['Görev', `${num((item.tasks || []).length)} görev · öğrenci başına ${num(totalMinutes(item.tasks))} dk`],
      ['Kaynak', item.templateId ? 'Şablondan uygulandı' : 'Elle oluşturuldu'],
      ['Oluşturan', `${item.createdBy || '—'} · ${fmtDateTime(item.createdAt)}`],
    ])));
    const tasks = dataTable({
      columns: [
        { key: 'when', label: 'Gün', width: '124px', cell: (row) => whenText(row) },
        { key: 'time', label: 'Saat', width: '92px', cell: (row) => timeText(row) },
        { key: 'type', label: 'Tür', width: '112px', cell: (row) => TASK_TYPES[row.type] || row.type },
        { key: 'title', label: 'Görev', width: 'minmax(0, 2fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', taskTitle(row)));
          const detail = [detailText(row, subjects), row.note].filter(Boolean).join(' · ');
          if (detail) cell.append(h('small', '', detail));
          return cell;
        } },
        { key: 'minutes', label: 'Süre', width: '64px', align: 'num', cell: (row) => `${num(row.estimatedMinutes)} dk` },
      ],
      rows: item.tasks || [],
      dense: true,
      empty: empty('Görev yok'),
    });
    nodes.push(card('Görevler', tasks.node, 'Öğrenci başına'));
    nodes.push(card('Öğrenciler', students.node, 'Öğrenci bazında görev durumu'));
    return nodes;
  }
  return panel;
}

// ============================================================== uyum tablosu

/**
 * `pagedList` yalnız `items` taşır; uyum yanıtının `weeks` ve `computedAt`
 * alanları için `pages` sarılır (aynı tek istek, ek istek YOK).
 */
function withMeta(app, onMeta) {
  const pages = (path, query = {}, { limit = 50 } = {}) => {
    let cursor = null;
    let done = false;
    return {
      get done() { return done; },
      async next() {
        if (done) return [];
        const data = await app.api.get(path, { ...query, limit, cursor: cursor || undefined });
        if (!cursor) onMeta(data || {});
        cursor = data?.nextCursor || null;
        done = !cursor;
        return Array.isArray(data?.items) ? data.items : [];
      },
    };
  };
  return { ...app, api: { ...app.api, pages } };
}

function adherenceTab(app, ctx) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', studentGroupId: '', subject: '', sort: 'name', weeks: 4, includeSubjects: false };
  let list = null;
  let columnsFor = null;      // tablonun çizildiği hafta listesi

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; rebuild();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '', (value) => {
    filters.studentGroupId = value; rebuild();
  }, { label: 'Çalışma grubu' });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => {
    filters.subject = value; rebuild();
  }, { label: 'Ders' });
  const weekSelect = selectBox([4, 8, 12].map((count) => ({ value: count, label: `Son ${count} hafta` })), 4,
    (value) => { filters.weeks = Number(value); rebuild(); }, { label: 'Aralık' });
  const sortSelect = selectBox(optionsOf(SORTS), 'name', (value) => { filters.sort = value; rebuild(); },
    { label: 'Sıralama' });
  const breakdown = h('label', 'ak-inline ak-plan-check');
  const breakdownBox = h('input', 'kit-check');
  breakdownBox.type = 'checkbox';
  breakdownBox.addEventListener('change', () => { filters.includeSubjects = breakdownBox.checked; rebuild(); });
  breakdown.append(breakdownBox, h('span', '', 'Ders kırılımı'));
  node.append(toolbar(classSelect.node, groupSelect.node, subjectSelect.node, weekSelect.node, sortSelect.node,
    breakdown));

  const legend = h('div', 'ak-plan-legend');
  legend.append(heatCell(85), h('span', '', '%80 ve üstü'), heatCell(65), h('span', '', '%50–79'),
    heatCell(30), h('span', '', '%50 altı'), heatCell(null), h('span', '', 'plan yok'),
    liveCell(40), h('span', '', 'bu hafta (sürüyor)'));
  const computed = h('span', 'ak-muted');
  const meta = h('div', 'ak-inline ak-plan-meta');
  meta.append(legend, h('span', 'kit-spacer'), computed);
  const slot = h('div');
  node.append(meta, slot);

  function range() {
    const to = todayIst();
    return { from: addDays(weekStart(to), -7 * (filters.weeks - 1)), to };
  }
  function mondays({ from, to }) {
    const out = [];
    for (let day = weekStart(from); day <= to; day = addDays(day, 7)) out.push(day);
    return out;
  }

  function columns(weeks) {
    const current = weekStart(todayIst());
    const list = [
      { key: 'student', label: 'Öğrenci', width: 'minmax(170px, 1.6fr)', cell: (row) => studentCell(row.student) },
      ...weeks.map((monday) => {
        // İçinde bulunulan hafta SÜRÜYOR (V9; U1'in KM eşi): pazartesi sabahı
        // herkes "%10" görünmesin diye nötr tonla çizilir ve ortalamaya girmez
        // (Akademi `averagePercent` yalnız tamamlanmış haftalar).
        const live = monday === current;
        return {
          key: `w-${monday}`,
          label: live ? 'Bu hafta' : fmtDate(monday).slice(0, 5),
          title: `${fmtDate(monday)} – ${fmtDate(addDays(monday, 6))} haftası${live
            ? ' · sürüyor, ortalamaya girmez' : ''}`,
          width: '64px',
          className: `ak-plan-heat-col${live ? ' ak-plan-live' : ''}`,
          cell: (row) => {
            const cell = (row.cells || []).find((item) => item.weekStart === monday);
            if (!cell) return heatCell(null, 'Bu hafta için özet yok');
            const minutes = cell.plannedMinutes
              ? `Planlanan ${num(cell.plannedMinutes)} dk · yapılan ${num(cell.doneMinutes)} dk` : 'Bu hafta plan yok';
            return live ? liveCell(cell.adherencePercent, `Bu hafta sürüyor · şimdiye kadar ${minutes.toLocaleLowerCase('tr-TR')}`)
              : heatCell(cell.adherencePercent, minutes);
          },
        };
      }),
      { key: 'average', label: 'Ortalama', width: '78px', className: 'ak-plan-heat-col',
        cell: (row) => heatCell(row.averagePercent, 'Aralığın ortalaması') },
    ];
    if (filters.includeSubjects) {
      list.push({ key: 'subjects', label: 'Ders kırılımı', width: 'minmax(0, 2fr)', cell: (row) => {
        const wrap = h('div', 'ak-plan-subjects');
        for (const item of row.bySubject || []) {
          const chip = h('span', `ak-plan-subject ${heatTone(item.adherencePercent)}`,
            `${item.subject?.shortName || item.subject?.name || item.subject?.id} ${item.adherencePercent === null
              || item.adherencePercent === undefined ? '—' : percent(item.adherencePercent, 0)}`);
          wrap.append(chip);
        }
        if (!wrap.childElementCount) wrap.append(h('span', 'ak-muted', '—'));
        return wrap;
      } });
    }
    return list;
  }

  /** Süzgeç değişince tablo baştan kurulur (hafta sütunları aralığa bağlı). */
  function rebuild(weeks = null) {
    list?.destroy();
    const span = range();
    columnsFor = weeks || mondays(span);
    list = pagedList({
      app: withMeta(app, (data) => {
        computed.textContent = data.computedAt ? `Son hesaplama: ${fmtDateTime(data.computedAt)}` : '';
        // Akademi haftaları farklı hizaladıysa (ör. kurum haftası) onun listesiyle yeniden çiz.
        if (Array.isArray(data.weeks) && data.weeks.length && data.weeks.join() !== columnsFor.join() && !weeks) {
          queueMicrotask(() => rebuild(data.weeks));
        }
      }),
      path: '/v1/admin/plan/adherence',
      query: () => ({ ...range(), classGroupId: filters.classGroupId, studentGroupId: filters.studentGroupId,
        subject: filters.subject, sort: filters.sort, includeSubjects: filters.includeSubjects || undefined }),
      columns: columns(columnsFor),
      dense: true,
      onRow: (row) => ctx.showStudent(row.student),
      empty: { title: 'Bu süzgece uyan öğrenci yok', text: 'Sınıf, grup ya da ders süzgecini değiştirin.' },
    });
    slot.replaceChildren(list.node);
    return list.reload();
  }

  async function reload() {
    const [loaded, subjects] = await Promise.all([ctx.ready(), app.lookups.subjects().catch(() => [])]);
    if (classSelect.node.options.length <= 1) {
      classSelect.options([{ value: '', label: 'Tüm sınıflar' },
        ...loaded.classes.map((item) => ({ value: item.id, label: item.name }))]);
      groupSelect.options([{ value: '', label: 'Tüm gruplar' },
        ...loaded.groups.map((item) => ({ value: item.id, label: item.name }))]);
      subjectSelect.options([{ value: '', label: 'Tüm dersler' },
        ...subjects.map((item) => ({ value: item.id, label: item.name }))]);
    }
    return rebuild();
  }

  node.append(h('div', 'ak-muted ak-plan-foot',
    'Satıra tıklayınca öğrencinin plan atamaları açılır. Uyum: planlanan dakikaya göre yapılan dakika. '
      + 'İçinde bulunulan hafta sürüyor: gri yazılır ve ortalamaya girmez.'));
  return { node, reload, destroy: () => list?.destroy() };
}

// ============================================================ erteleme talepleri

function postponeTab(app, ctx, tabs, onStudent) {
  const node = h('div', 'ak-stack');
  const filters = { status: 'requested', classGroupId: '' };
  const statusSelect = selectBox([
    { value: 'requested', label: 'Bekleyenler' }, { value: 'approved', label: 'Onaylananlar' },
    { value: 'declined', label: 'Reddedilenler' }, { value: 'cancelled', label: 'Geri alınanlar' },
  ], 'requested', (value) => { filters.status = value; reload(); }, { label: 'Durum' });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const student = studentFilter(app, { value: ctx.student(), onChange: (next) => onStudent(next) });
  node.append(toolbar(statusSelect.node, classSelect.node, student.node));

  const list = pagedList({
    app,
    path: '/v1/admin/plan/postpone-requests',
    query: () => ({ status: filters.status, classGroupId: filters.classGroupId, studentId: student.value()?.id || '' }),
    onLoaded: (rows) => {
      if (filters.status === 'requested') tabs.badge('postpone', rows.filter((row) => row.status === 'requested').length);
    },
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: (row) => studentCell(row.student) },
      { key: 'task', label: 'Koç görevi', width: 'minmax(0, 1.8fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const task = row.task || {};
        cell.append(h('span', '', taskTitle(task)),
          h('small', '', [TASK_TYPES[task.type], task.subject?.shortName || task.subject?.name,
            `${fmtDate(task.date)} ${weekdayOf(task.date)}${task.startTime ? ` ${task.startTime}` : ''}`,
            task.assignment?.assignedBy?.displayName ? `atayan ${task.assignment.assignedBy.displayName}` : '']
            .filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'requested', label: 'İstenen gün', width: '130px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', `${fmtDate(row.requestedDate)} ${weekdayOf(row.requestedDate)}`),
          h('small', '', row.requestedStartTime || 'saat belirtilmedi'));
        return cell;
      } },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.reason || '—'), h('small', '', fmtDateTime(row.createdAt)));
        return cell;
      } },
      { key: 'status', label: 'Durum', width: 'minmax(0, 1.2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(statusBadge(POSTPONE_STATUS, row.status));
        const decided = [row.approvedDate ? `→ ${fmtDate(row.approvedDate)}` : '', row.decisionNote,
          row.decidedBy?.displayName].filter(Boolean).join(' · ');
        if (decided) cell.append(h('small', '', decided));
        return cell;
      } },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.status !== 'requested') return actions;
        actions.append(
          actionButton(app, 'admin_approvePlanPostpone', 'Onayla', { variant: 'primary', onClick: () => approve(row) }),
          actionButton(app, 'admin_declinePlanPostpone', 'Reddet', { variant: 'ghost', onClick: () => decline(row) }),
        );
        return actions;
      } },
    ],
    empty: { title: 'Bu durumda erteleme talebi yok', text: 'Öğrenciler koç görevini ertelemek istediğinde burada görünür.' },
  });
  node.append(list.node);

  async function approve(row) {
    const task = row.task || {};
    const next = await formDialog(app, {
      title: `${row.student?.displayName || 'Öğrenci'}: ertelemeyi onayla`,
      description: `“${taskTitle(task)}” (${fmtDate(task.date)}) seçilen güne taşınır ve öğrenciye bildirim gider.\n`
        + `Gerekçe: ${row.reason || '—'}`,
      value: { day: row.requestedDate, clock: row.requestedStartTime || '', note: '' },
      fields: [
        { key: 'day', label: 'Taşınacak gün', type: 'date', required: true },
        { key: 'clock', label: 'Başlangıç saati (isteğe bağlı)', maxLength: 5, placeholder: 'SS:DD',
          validate: (raw) => (raw && !CLOCK.test(raw) ? 'Saati 16:30 gibi yazın.' : null) },
        { key: 'note', label: 'Öğrenciye not (isteğe bağlı)', maxLength: 300, wide: true },
      ],
      submitLabel: 'Onayla',
      onSubmit: (draft, { key }) => {
        if (!draft.day) throw new AkademiError({ code: 'validation_error', message: 'Taşınacak günü seçin.' });
        return app.api.post(`/v1/admin/plan/postpone-requests/${row.id}/approve`, {
          date: draft.day !== row.requestedDate ? draft.day : null,
          startTime: draft.clock || null,
          note: String(draft.note || '').trim() || null,
        }, { key });
      },
    });
    if (next) {
      app.toast(`Erteleme onaylandı: görev ${fmtDate(next.approvedDate || row.requestedDate)} gününe taşındı.`, 'good');
      list.replace((item) => item.id === row.id, next);
    }
  }

  async function decline(row) {
    const reason = await reasonDialog(app, {
      title: `${row.student?.displayName || 'Öğrenci'}: ertelemeyi reddet`,
      description: 'Görev planlandığı günde kalır. Gerekçe öğrenciye bildirilir (3–300 karakter).',
      confirmLabel: 'Reddet',
      presets: ['Görev bu gün yapılmalı', 'Koçunla görüşelim'],
    });
    if (!reason) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/plan/postpone-requests/${row.id}/decline`,
      { reason: String(reason).slice(0, 300) }, { key: `plan-postpone-no-${row.id}` }),
    { success: 'Erteleme talebi reddedildi.' });
    if (next) list.replace((item) => item.id === row.id, next);
  }

  async function reload() {
    const loaded = await ctx.ready();
    if (classSelect.node.options.length <= 1) {
      classSelect.options([{ value: '', label: 'Tüm sınıflar' },
        ...loaded.classes.map((item) => ({ value: item.id, label: item.name }))]);
    }
    return list.reload();
  }

  return {
    node,
    reload,
    setStudent(next) { student.set(next); reload(); },
    destroy() { list.destroy(); student.destroy(); },
  };
}

