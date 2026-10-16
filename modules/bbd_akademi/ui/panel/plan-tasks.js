// BBD Akademi — Çalışma planı: görev düzenleyici, hedef seçici, görev metinleri.
//
// Atama (tarihli görev, F-A18) ve şablon (gün ofsetli görev) AYNI düzenleyiciyi
// kullanır; `mode`:
//   · date   — yeni atama: her tür; gün + isteğe bağlı saat aralığı
//   · offset — şablon: "kaçıncı gün". Deneme ve ödev belirli bir kayda bağlı
//              olduğu için şablonda YOKTUR; sözleşmenin şablon görevinde
//              examId, homeworkId, sayfa aralığı ve hata tekrarı ayrıntısı yok.
//   · patch  — atamayı düzenle: Akademi yalnız gün, saat, süre, başlık, hedef
//              soru ve notu değiştirir (yapılmamış görevler; tür/içerik sabit).
//
// TÜRE GÖRE ZORUNLU ALAN. Düzenleyici ortak görev formudur (`task-form.js`); tür →
// zorunlu alan tablosu `task-form-logic.js rulesFor` içinde, sunucunun
// `fields_required_for_type` kurallarıyla birebir (Akademi README §14 madde 97):
// soru çözümü → hedef soru, kaynak → kaynak + (bölüm ya da sayfa aralığı),
// tekrar → konu, okuma → hedef sayfa, deneme → deneme, ödev → ödev, hata tekrarı →
// hata sayısı; konu çalışması ders, serbest görev başlık ister.
//
// BAŞLIK. Boş bırakılırsa seçimden kurulur ("Üslü sayılar — 40 soru"): atama
// yükü konu/kaynak/deneme ADINI taşımaz, ekranlarda okunacak metin başlıktır.
// Ders adı ise önbellekli ders listesinden (`app.lookups.subjects`) çözülür.

import { button, h } from '../../ui-kit/kit.js';
import { taskEditor } from './shared/task-form.js';
import { formFromPlanTask, intOf, nextForm, planTask } from './shared/task-form-logic.js';
import { dataTable } from '../../ui-kit/table.js';
import { badge, emptyState } from '../../ui-kit/layout.js';
import { WEEKDAYS, addDays, fmtDate, multiChips, num, studentPicker, todayIst } from './common.js';

export const TASK_TYPES = {
  topic_study: 'Konu çalışması',
  question_solving: 'Soru çözümü',
  resource_section: 'Kaynak bölümü',
  review: 'Tekrar',
  mistake_review: 'Hata tekrarı',
  reading: 'Okuma',
  exam: 'Deneme',
  homework: 'Ödev',
  free: 'Serbest görev',
};

/** Şablonda kullanılabilen türler (deneme ve ödev belirli kayda bağlıdır). */
export const TEMPLATE_TYPES = Object.keys(TASK_TYPES).filter((key) => key !== 'exam' && key !== 'homework');

export const HOLIDAY_POLICIES = { shift_next: 'Sonraki güne kaydır', skip: 'Atla' };

export const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;


// ------------------------------------------------------------------ metin

/** 'YYYY-MM-DD' → 'Sal'. */
export function weekdayOf(iso) {
  const [year, month, day] = String(iso || '').split('-').map(Number);
  if (!year || !month || !day) return '';
  return WEEKDAYS[(new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7][1];
}

/** Görevin günü: tarihli → '14.10.2026 Sal', şablon → '3. gün'. */
export function whenText(task) {
  if (task?.date) return `${fmtDate(task.date)} ${weekdayOf(task.date)}`;
  if (task?.dayOffset !== undefined && task?.dayOffset !== null) return `${Number(task.dayOffset) + 1}. gün`;
  return '—';
}

export function timeText(task) {
  if (task?.startTime && task?.endTime) return `${task.startTime}–${task.endTime}`;
  return task?.startTime || '—';
}

export function taskTitle(task) {
  return task?.title || TASK_TYPES[task?.type] || 'Görev';
}

/** Ayrıntı satırı (ders, hedef, sayfa…) — yalnız görev yükünden ve ders önbelleğinden. */
export function detailText(task, subjects) {
  if (!task) return '';
  const subjectId = task.subjectId || task.mistakeReview?.subjectId;
  const subject = subjects?.get?.(subjectId);
  const parts = [];
  if (subjectId) parts.push(subject?.shortName || subject?.name || task._view?.subjectName || subjectId);
  if (task._view?.topicName) parts.push(task._view.topicName);
  if (task.targetQuestions) parts.push(`${num(task.targetQuestions)} soru`);
  if (task.sectionIds?.length) parts.push(`${num(task.sectionIds.length)} bölüm`);
  if (task.pageFrom || task.pageTo) parts.push(`s. ${task.pageFrom || '?'}–${task.pageTo || '?'}`);
  if (task.targetPages) parts.push(`${num(task.targetPages)} sayfa`);
  if (task.mistakeReview?.count) parts.push(`${num(task.mistakeReview.count)} hata`);
  return parts.join(' · ');
}

/** Görev listesinin gün aralığı ('14.10.2026 – 20.10.2026'). */
export function rangeText(tasks) {
  const days = (tasks || []).map((task) => task.date).filter(Boolean).sort();
  if (!days.length) return '—';
  return days[0] === days[days.length - 1] ? fmtDate(days[0])
    : `${fmtDate(days[0])} – ${fmtDate(days[days.length - 1])}`;
}

export function totalMinutes(tasks) {
  return (tasks || []).reduce((sum, task) => sum + (Number(task.estimatedMinutes) || 0), 0);
}

function sortKey(task) {
  const day = task.date || String(task.dayOffset ?? 0).padStart(3, '0');
  return `${day}|${task.startTime || '99:99'}`;
}

export function sortTasks(list) {
  return [...(list || [])].sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
}

/** Ders listesi (önbellekli) → id → ders. Okunamazsa boş. */
export async function subjectMap(app) {
  const list = await app.lookups.subjects().catch(() => []);
  return new Map(list.map((item) => [item.id, item]));
}

// ---------------------------------------------------------------- gövde

/** Düzenleyicideki görev → sözleşme gövdesi (`_view` ve moda yabancı alanlar atılır). */
export function taskBody(task, mode = 'date') {
  const body = {
    startTime: task.startTime || null,
    endTime: task.endTime || null,
    estimatedMinutes: Number(task.estimatedMinutes),
    type: task.type,
    title: task.title || null,
    subjectId: task.subjectId || null,
    topicId: task.topicId || null,
    resourceId: task.resourceId || null,
    sectionIds: task.sectionIds || [],
    targetQuestions: task.targetQuestions ?? null,
    targetPages: task.targetPages ?? null,
    note: task.note || null,
  };
  if (mode === 'offset') {
    body.dayOffset = Number(task.dayOffset);
  } else {
    Object.assign(body, {
      date: task.date,
      pageFrom: task.pageFrom ?? null,
      pageTo: task.pageTo ?? null,
      examId: task.examId || null,
      homeworkId: task.homeworkId || null,
      mistakeReview: task.mistakeReview || null,
    });
  }
  return body;
}

const PATCH_KEYS = ['date', 'startTime', 'endTime', 'estimatedMinutes', 'title', 'targetQuestions', 'note'];

/** Atama düzenleme: yalnız DEĞİŞEN görevler ve alanları (`{id, …}`). */
export function taskPatches(original, edited) {
  const before = new Map((original || []).map((task) => [task.id, task]));
  const out = [];
  for (const task of edited || []) {
    const old = before.get(task.id);
    if (!old) continue;
    const change = { id: task.id };
    for (const key of PATCH_KEYS) {
      const left = old[key] ?? null;
      const right = task[key] ?? null;
      if (String(left ?? '') !== String(right ?? '')) change[key] = right;
    }
    if (Object.keys(change).length > 1) out.push(change);
  }
  return out;
}

// ----------------------------------------------------------- görev düzenleyici

/**
 * Satır içi görev düzenleyicisi: ortak görev formu (`task-form.js` — tür kartları,
 * zorunlular üstte, kaymayan düzen, satır içi doğrulama). `onSave(task, again)`:
 * `again` doluysa "Ekle ve yeni" — sıradaki görevin form taslağı.
 */
function buildEditor(app, { mode, task, form = null, duration, isNew, target = null, onSave, onCancel }) {
  const patch = mode === 'patch';
  const node = h('div', 'ak-plan-editor');
  node.append(h('div', 'ak-plan-editor-head', isNew ? 'Yeni görev' : 'Görevi düzenle'));
  const editor = taskEditor(app, { mode, form: form || formFromPlanTask(task, patch ? { title: task.title || '' } : {}), duration, isNew,
    originalDate: task.date || '', target, onSubmit: (again) => save(again && isNew && !patch) });
  const error = h('div', 'ak-plan-editor-error');
  error.setAttribute('role', 'alert');
  const actions = h('div', 'ak-plan-editor-actions');
  actions.append(button('Vazgeç', { variant: 'ghost', onClick: () => onCancel() }));
  if (isNew && !patch) actions.append(button('Ekle ve yeni', { onClick: () => save(true) }));
  actions.append(button(isNew ? 'Listeye ekle' : 'Görevi güncelle', { variant: 'primary', onClick: () => save(false) }));
  node.append(editor.node, error, actions);

  function save(again) {
    error.textContent = '';
    const result = editor.check();
    if (!result.ok) {
      const count = Object.keys(result.errors).length;
      error.textContent = count > 1 ? `${count} alanı düzeltin (işaretli).` : 'İşaretli alanı düzeltin.';
      editor.focusFirst(result.first);
      return;
    }
    const value = editor.value();
    if (patch) {
      onSave({ ...task, date: value.date, startTime: value.startTime || null, endTime: value.endTime || null,
        estimatedMinutes: intOf(value.estimatedMinutes), title: String(value.title || '').trim() || null,
        targetQuestions: intOf(value.targetQuestions) || null, note: String(value.note || '').trim() || null }, null);
      return;
    }
    onSave(planTask(value, mode), again ? nextForm(value) : null);
  }

  return { node, destroy: () => editor.destroy() };
}

// ----------------------------------------------------------- görev listesi alanı

/**
 * formDialog için görev listesi alanı (`type: 'custom'`): üstte eklenen
 * görevler, altta tek bir düzenleyici. `onChange(tasks)` her değişiklikte
 * tam listeyi verir. formDialog `update(draft)` çağırınca şablon süresi
 * (`durationDays`) okunur ve süre dışı kalan görevler işaretlenir.
 */
export function taskListField(app, { mode = 'date', tasks = [], durationDays = null, defaultDate = todayIst(1),
  target = null, onChange } = {}) {
  const patch = mode === 'patch';
  const offset = mode === 'offset';
  let items = sortTasks(tasks).map((task) => ({ ...task }));
  let duration = durationDays ? Number(durationDays) : null;
  let subjects = new Map();
  let editor = null;
  let editing = null;            // düzenlenen satırın sırası; null = yeni

  const node = h('div', 'ak-plan-tasks');
  const table = dataTable({
    columns: [
      { key: 'when', label: offset ? 'Gün' : 'Tarih', width: offset ? '92px' : '132px', cell: (row) => {
        if (offset && duration && Number(row.dayOffset) >= duration) {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', whenText(row)), badge('Süre dışında', 'bad'));
          return cell;
        }
        return whenText(row);
      } },
      { key: 'time', label: 'Saat', width: '96px', cell: (row) => timeText(row) },
      { key: 'type', label: 'Tür', width: '120px', cell: (row) => TASK_TYPES[row.type] || row.type },
      { key: 'title', label: 'Görev', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', taskTitle(row)));
        const detail = detailText(row, subjects);
        if (detail) cell.append(h('small', '', detail));
        return cell;
      } },
      { key: 'minutes', label: 'Süre', width: '64px', align: 'num', cell: (row) => `${num(row.estimatedMinutes)} dk` },
      { key: 'actions', label: '', width: patch ? '90px' : '200px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button('Düzenle', { variant: 'ghost', onClick: () => open(row._index) }));
        if (!patch) {
          actions.append(
            button('Kopyala', { variant: 'ghost', title: 'Ertesi güne kopyasını aç', onClick: () => copy(row._index) }),
            button('Çıkar', { variant: 'ghost', onClick: () => remove(row._index) }),
          );
        }
        return actions;
      } },
    ],
    rows: [],
    dense: true,
    rowKey: (row) => String(row._index),
    empty: emptyState({ title: 'Henüz görev yok', text: patch ? '' : 'Aşağıdaki “Görev ekle” ile başlayın.' }),
  });
  const summary = h('div', 'ak-plan-total');
  const editorSlot = h('div');
  const addButton = button('+ Görev ekle', { onClick: () => open(null) });
  addButton.hidden = patch;
  node.append(table.node, summary, editorSlot, addButton);

  subjectMap(app).then((map) => { subjects = map; paint(); });

  function paint() {
    table.update({ rows: items.map((task, index) => ({ ...task, _index: index })) });
    if (!items.length) { summary.textContent = ''; return; }
    const parts = [`${num(items.length)} görev`, `toplam ${num(totalMinutes(items))} dk`];
    if (!offset) parts.push(rangeText(items));
    const outside = offset && duration ? items.filter((task) => Number(task.dayOffset) >= duration).length : 0;
    if (outside) parts.push(`${num(outside)} görev süre dışında`);
    summary.textContent = parts.join(' · ');
  }

  function fire() { onChange?.(items.map((task) => ({ ...task }))); }

  function close() {
    editor?.destroy();
    editor = null;
    editing = null;
    editorSlot.replaceChildren();
    addButton.hidden = patch;
  }

  function open(index, seed = null, form = null) {
    close();
    editing = index;
    const last = items[items.length - 1];
    const task = index !== null ? items[index]
      : seed || { type: last?.type || 'question_solving', estimatedMinutes: last?.estimatedMinutes || 40,
        date: last?.date || defaultDate, dayOffset: last ? Number(last.dayOffset || 0) : 0,
        subjectId: last?.subjectId || '', _view: { subjectName: last?._view?.subjectName || '' } };
    editor = buildEditor(app, { mode, task, form, duration, target, isNew: index === null,
      onSave: (next, again) => {
        if (editing === null) items.push(next);
        else items[editing] = next;
        items = sortTasks(items);
        close();
        paint();
        fire();
        if (again) open(null, null, again);
      },
      onCancel: () => close() });
    editorSlot.replaceChildren(editor.node);
    addButton.hidden = true;
    editor.node.scrollIntoView?.({ block: 'nearest' });
  }

  function copy(index) {
    const source = items[index];
    const seed = { ...source, id: undefined };
    if (offset) seed.dayOffset = Number(source.dayOffset || 0) + 1;
    else seed.date = addDays(source.date || defaultDate, 1);
    open(null, seed);
  }

  function remove(index) {
    items = items.filter((_, position) => position !== index);
    close();
    paint();
    fire();
  }

  paint();
  return {
    node,
    value: () => items.map((task) => ({ ...task })),
    isEditing: () => Boolean(editor),
    /** Şablon: süre dışında kalan görev sayısı. */
    outside: () => (offset && duration ? items.filter((task) => Number(task.dayOffset) >= duration).length : 0),
    update(draft) {
      if (!offset) return;
      const next = Number(draft?.durationDays) || null;
      if (next !== duration) { duration = next; paint(); }
    },
    destroy: () => close(),
  };
}

// --------------------------------------------------------------- hedef

/** Sınıf ve çalışma grubu listeleri (önbellekli); okunamazsa boş liste. */
export function loadTargets(app) {
  return Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]);
}

/**
 * Hedef: sınıflar + çalışma grupları + öğrenciler (birlikte seçilebilir).
 * `onChange({classGroupIds, studentGroupIds, studentIds})`.
 */
export function targetsField(app, { classes = [], groups = [], value = {}, onChange } = {}) {
  const node = h('div', 'ak-plan-targets');
  const state = {
    classGroupIds: [...(value.classGroupIds || [])],
    studentGroupIds: [...(value.studentGroupIds || [])],
  };
  const students = new Map((value.students || []).map((item) => [item.id, item.displayName || item.id]));
  const current = () => ({ ...state, studentIds: [...students.keys()] });
  const fire = () => onChange?.(current());

  const blockOf = (title, content) => {
    const wrap = h('div', 'ak-plan-target-block');
    wrap.append(h('div', 'ak-plan-target-title', title), content);
    return wrap;
  };
  const classChips = classes.length
    ? multiChips(classes.map((item) => ({ value: item.id, label: item.name })), state.classGroupIds,
      (list) => { state.classGroupIds = list; fire(); }).node
    : h('span', 'ak-muted', 'Sınıf tanımlı değil.');
  const groupChips = groups.length
    ? multiChips(groups.map((item) => ({ value: item.id, label: item.name })), state.studentGroupIds,
      (list) => { state.studentGroupIds = list; fire(); }).node
    : h('span', 'ak-muted', 'Çalışma grubu yok.');

  const picked = h('div', 'ak-chips');
  const paintPicked = () => {
    picked.replaceChildren();
    for (const [id, name] of students) {
      const chip = h('button', 'kit-chip on', `${name} ✕`);
      chip.type = 'button';
      chip.title = 'Listeden çıkar';
      chip.addEventListener('click', () => { students.delete(id); paintPicked(); fire(); });
      picked.append(chip);
    }
    if (!students.size) picked.append(h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
  };
  const picker = studentPicker(app, { onPick: (student) => {
    students.set(student.id, student.displayName || student.fullName || student.id);
    paintPicked();
    fire();
  } });
  paintPicked();
  const studentBox = h('div', 'ak-stack');
  studentBox.append(picker.node, picked);

  node.append(blockOf('Sınıflar', classChips), blockOf('Çalışma grupları', groupChips),
    blockOf('Öğrenciler', studentBox));
  return {
    node,
    value: current,
    count: () => state.classGroupIds.length + state.studentGroupIds.length + students.size,
    destroy: picker.destroy,
  };
}

/** Hedef özeti: 'Sınıf 11-A, 11-B · 3 öğrenci'. `names`: {classes: Map, groups: Map}. */
export function targetText(target, names = {}) {
  const parts = [];
  const named = (ids, map, noun) => {
    if (!ids?.length) return;
    const known = ids.map((id) => map?.get?.(id)).filter(Boolean);
    if (known.length === ids.length && known.length <= 3) parts.push(known.join(', '));
    else if (known.length === ids.length) parts.push(`${known.slice(0, 2).join(', ')} +${num(ids.length - 2)}`);
    else parts.push(`${num(ids.length)} ${noun}`);
  };
  named(target?.classGroupIds, names.classes, 'sınıf');
  named(target?.studentGroupIds, names.groups, 'grup');
  if (target?.studentIds?.length) parts.push(`${num(target.studentIds.length)} öğrenci`);
  return parts.join(' · ') || '—';
}
