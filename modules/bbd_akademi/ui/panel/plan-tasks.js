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
// TÜRE GÖRE ZORUNLU ALAN. Asıl kapı Akademi'dir (`fields_required_for_type`);
// `TYPE_SPEC` yalnız formu sadeleştirir ve erken uyarır. Bilerek sunucudan
// GEVŞEK tutuldu (sunucu kabul ederken ekranın reddetmesi daha kötü): yalnız
// türün anlamsız kalacağı alan zorunludur — deneme → deneme, ödev → ödev,
// kaynak bölümü → kaynak, soru çözümü → ders + hedef soru, konu çalışması ve
// tekrar → ders, serbest görev → başlık.
//
// BAŞLIK. Boş bırakılırsa seçimden kurulur ("Üslü sayılar — 40 soru"): atama
// yükü konu/kaynak/deneme ADINI taşımaz, ekranlarda okunacak metin başlıktır.
// Ders adı ise önbellekli ders listesinden (`app.lookups.subjects`) çözülür.

import { button, h } from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateField } from '../../ui-kit/datefield.js';
import { badge, emptyState } from '../../ui-kit/layout.js';
import { fillProgramSelect } from './curriculum-picker.js';
import {
  WEEKDAYS, addDays, clockInput, fmtDate, multiChips, num, problem, searchBox,
  studentPicker, todayIst,
} from './common.js';

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

const SUBJECT_LEVELS = { ortaokul: 'Ortaokul', lise: 'Lise', exam: 'Sınav dersleri', custom: 'Kuruma özel' };

// Tür → görünen içerik alanları (`show`) ve zorunlular (`need`).
const TYPE_SPEC = {
  topic_study: { show: ['topic'], need: ['subject'] },
  question_solving: { show: ['topic', 'questions'], need: ['subject', 'questions'] },
  resource_section: { show: ['resource', 'pages', 'questions'], need: ['resource'] },
  review: { show: ['topic'], need: ['subject'] },
  mistake_review: { show: ['topic', 'mistakeCount'], need: [] },
  reading: { show: ['readPages'], need: [] },
  exam: { show: ['exam'], need: ['exam'] },
  homework: { show: ['homework'], need: ['homework'] },
  free: { show: [], need: ['title'] },
};
const CONTENT_KEYS = ['topic', 'resource', 'pages', 'questions', 'readPages', 'exam', 'homework',
  'mistakeCount'];

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

function autoTitle(type, task, view) {
  const focus = view.topicName || view.subjectName || '';
  let text = '';
  switch (type) {
    case 'topic_study': text = focus ? `${focus} — konu çalışması` : ''; break;
    case 'question_solving':
      text = `${focus ? `${focus} — ` : ''}${task.targetQuestions ? `${num(task.targetQuestions)} soru` : 'soru çözümü'}`;
      break;
    case 'resource_section':
      text = [view.resourceTitle, (view.sectionTitles || []).slice(0, 2).join(', ')
        + ((view.sectionTitles || []).length > 2 ? ` +${view.sectionTitles.length - 2}` : ''),
      task.pageFrom ? `s. ${task.pageFrom}–${task.pageTo || '?'}` : ''].filter(Boolean).join(' · ');
      break;
    case 'review': text = focus ? `${focus} — tekrar` : ''; break;
    case 'mistake_review': text = `Hata tekrarı${focus ? ` — ${focus}` : ''}`; break;
    case 'reading': text = task.targetPages ? `Okuma — ${num(task.targetPages)} sayfa` : ''; break;
    case 'exam': text = view.examName || ''; break;
    case 'homework': text = view.homeworkTitle || ''; break;
    default: text = '';
  }
  return text ? text.slice(0, 120) : null;
}

// --------------------------------------------------------------- küçük girişler

function numberInput(value, { placeholder = '', label = '' } = {}) {
  const input = h('input', 'kit-input ak-plan-num');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.value = value === null || value === undefined ? '' : String(value);
  if (placeholder) input.placeholder = placeholder;
  if (label) input.setAttribute('aria-label', label);
  return input;
}

function intOf(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return null;
  return /^\d+$/.test(text) ? Number(text) : NaN;
}

/** Yazdıkça arayan seçici (250 ms). `describe(item)` → [başlık, alt satır]. */
function searchPicker({ placeholder, search, describe, onPick }) {
  const node = h('div', 'ak-picker');
  const results = h('div', 'ak-picker-list');
  results.hidden = true;
  let token = 0;
  const box = searchBox({ placeholder, onSearch: async (text) => {
    const mine = ++token;
    if (text.length < 2) { results.hidden = true; return; }
    try {
      const items = await search(text);
      if (mine !== token) return;
      results.replaceChildren();
      if (!items.length) results.append(h('div', 'ak-picker-empty', 'Eşleşen kayıt yok.'));
      for (const item of items) {
        const [title, sub] = describe(item);
        const row = h('button', 'ak-picker-row');
        row.type = 'button';
        row.append(h('b', '', title), h('span', '', sub || ''));
        row.addEventListener('click', () => { results.hidden = true; onPick(item); });
        results.append(row);
      }
      results.hidden = false;
    } catch (failure) {
      if (mine !== token) return;
      results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
      results.hidden = false;
    }
  } });
  node.append(box.node, results);
  return { node, destroy: box.destroy };
}

/**
 * Açılır kutu, seçenekleri İLK GEREKTİĞİNDE yüklenir (`ensure()`): deneme ve
 * ödev listesi yalnız o tür seçilince istenir.
 */
function remoteSelect({ load, value = '', empty, label, describe, missing }) {
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', label);
  const items = new Map();
  let started = false;
  const fill = (options) => {
    select.replaceChildren();
    for (const option of options) {
      const item = h('option', '', option.label);
      item.value = option.value;
      select.append(item);
    }
  };
  fill([{ value: value || '', label: value ? missing : empty }]);
  async function ensure() {
    if (started) return;
    started = true;
    select.disabled = true;
    fill([{ value: '', label: 'Yükleniyor…' }]);
    try {
      const list = await load();
      for (const item of list) items.set(item.id, item);
      const options = [{ value: '', label: empty }, ...list.map((item) => ({ value: item.id, label: describe(item) }))];
      if (value && !items.has(value)) options.push({ value, label: missing });
      fill(options);
      select.value = value || '';
    } catch (failure) {
      started = false;
      fill([{ value: value || '', label: problem(failure, 'Liste alınamadı.') }]);
    } finally {
      select.disabled = false;
    }
  }
  return { node: select, ensure, value: () => select.value, item: () => items.get(select.value) || null };
}

// --------------------------------------------------------------- ders / konu

/**
 * Ders → program → ünite/konu. `topicId` sözleşmede program, ünite ya da konu
 * kimliği olabilir: program seçili, konu boşsa programın tamamı gönderilir.
 * Kayıtlı ama programı bilinmeyen konu "Kayıtlı konu" seçeneği olarak korunur.
 */
export function topicPicker(app, { subjectId = '', topicId = '', topicName = '' } = {}) {
  const node = h('div', 'ak-plan-topic');
  const subjectSel = h('select', 'kit-select');
  subjectSel.setAttribute('aria-label', 'Ders');
  const programSel = h('select', 'kit-select');
  programSel.setAttribute('aria-label', 'Program');
  const topicSel = h('select', 'kit-select');
  topicSel.setAttribute('aria-label', 'Ünite ya da konu');
  node.append(subjectSel, programSel, topicSel);

  const state = { subjectId, programId: '', programTitle: '',
    keep: topicId ? { id: topicId, name: topicName } : null };
  const names = new Map();
  let programToken = 0;
  let topicToken = 0;

  const option = (parent, value, label) => {
    const item = h('option', '', label);
    item.value = value;
    parent.append(item);
    return item;
  };

  function paintKeep() {
    topicSel.replaceChildren();
    names.clear();
    if (state.keep) {
      option(topicSel, state.keep.id, state.keep.name || `Kayıtlı konu (${state.keep.id})`);
      option(topicSel, '', 'Konu yok');
      topicSel.value = state.keep.id;
      topicSel.disabled = false;
    } else {
      option(topicSel, '', state.programId ? 'Programın tamamı' : 'Önce program seçin');
      topicSel.disabled = true;
    }
  }

  async function loadSubjects() {
    subjectSel.replaceChildren();
    option(subjectSel, '', 'Yükleniyor…');
    const list = await app.lookups.subjects().catch(() => []);
    subjectSel.replaceChildren();
    option(subjectSel, '', 'Ders seçin');
    const known = new Set();
    for (const [level, label] of Object.entries(SUBJECT_LEVELS)) {
      const group = list.filter((item) => (item.level || 'custom') === level);
      if (!group.length) continue;
      const optgroup = h('optgroup');
      optgroup.label = label;
      for (const subject of group) { option(optgroup, subject.id, subject.name); known.add(subject.id); }
      subjectSel.append(optgroup);
    }
    for (const subject of list.filter((item) => !known.has(item.id))) {
      option(subjectSel, subject.id, subject.name);
      known.add(subject.id);
    }
    if (state.subjectId && !known.has(state.subjectId)) {
      option(subjectSel, state.subjectId, `${state.subjectId} (listede yok)`);
    }
    subjectSel.value = state.subjectId || '';
    if (state.subjectId) loadPrograms();
    else { programSel.replaceChildren(); option(programSel, '', 'Önce ders seçin'); programSel.disabled = true; paintKeep(); }
  }

  async function loadPrograms() {
    const mine = ++programToken;
    programSel.replaceChildren();
    option(programSel, '', 'Yükleniyor…');
    programSel.disabled = true;
    paintKeep();
    try {
      const data = await app.api.getCached('/v1/admin/curricula/programs', { subject: state.subjectId },
        { ttlMs: 10 * 60_000 });
      if (mine !== programToken) return;
      const items = data?.items || [];
      // Seviye gruplu ("11. sınıf · Matematik"): aynı adlı programlar (13 "Matematik") ayrışır, liste
      // 5. sınıftan değil seviye sırasıyla gelir (curriculum-picker.js).
      fillProgramSelect(programSel, items, { placeholder: items.length ? 'Program seçin (isteğe bağlı)'
        : 'Bu derste program yok', recommendedFirst: false });
      programSel.disabled = !items.length;
    } catch (failure) {
      if (mine !== programToken) return;
      programSel.replaceChildren();
      option(programSel, '', problem(failure, 'Programlar alınamadı.'));
    }
  }

  async function loadTopics() {
    const mine = ++topicToken;
    if (!state.programId) { paintKeep(); return; }
    topicSel.replaceChildren();
    option(topicSel, '', 'Yükleniyor…');
    topicSel.disabled = true;
    try {
      const program = await app.api.getCached(`/v1/admin/curricula/programs/${state.programId}`, undefined,
        { ttlMs: 10 * 60_000 });
      if (mine !== topicToken) return;
      topicSel.replaceChildren();
      names.clear();
      option(topicSel, '', 'Programın tamamı');
      const units = [...(program?.units || [])].filter((unit) => !unit.retired)
        .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      for (const unit of units) {
        const group = h('optgroup');
        group.label = unit.title;
        option(group, unit.id, `${unit.title} (ünitenin tamamı)`);
        names.set(unit.id, unit.title);
        for (const topic of [...(unit.topics || [])].filter((item) => !item.retired)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
          option(group, topic.id, topic.title);
          names.set(topic.id, topic.title);
        }
        topicSel.append(group);
      }
      topicSel.disabled = false;
    } catch (failure) {
      if (mine !== topicToken) return;
      topicSel.replaceChildren();
      option(topicSel, '', problem(failure, 'Konular alınamadı.'));
    }
  }

  subjectSel.addEventListener('change', () => {
    state.subjectId = subjectSel.value;
    state.programId = '';
    state.programTitle = '';
    state.keep = null;
    if (state.subjectId) loadPrograms();
    else { programSel.replaceChildren(); option(programSel, '', 'Önce ders seçin'); programSel.disabled = true; paintKeep(); }
  });
  programSel.addEventListener('change', () => {
    state.programId = programSel.value;
    state.programTitle = programSel.value ? programSel.selectedOptions[0]?.textContent || '' : '';
    state.keep = null;
    loadTopics();
  });

  loadSubjects();

  return {
    node,
    value() {
      const picked = topicSel.disabled ? '' : topicSel.value;
      const topic = picked || state.programId || '';
      const topicLabel = picked ? (names.get(picked) || state.keep?.name || '') : state.programTitle;
      return {
        subjectId: subjectSel.value || '',
        subjectName: subjectSel.value ? subjectSel.selectedOptions[0]?.textContent || '' : '',
        topicId: topic,
        topicName: topicLabel,
      };
    },
  };
}

// --------------------------------------------------------------- kaynak

/**
 * Kaynak + bölümler. Arama `catalog_searchLibrary`; içindekiler seçimden
 * sonra TEK istekle gelir (`catalog_getLibraryResource`, önbellekli).
 * Sözleşme sınırı: en çok 20 bölüm.
 */
function resourceField(app, { resourceId = '', title = '', sectionIds = [] } = {}) {
  const node = h('div', 'ak-stack');
  const state = { resourceId, title, sectionIds: [...sectionIds], titles: new Map() };
  const current = h('div', 'ak-inline');
  const sections = h('div', 'ak-plan-sections');
  const picker = searchPicker({
    placeholder: 'Kaynak ara (ad, yayınevi, ISBN)',
    search: async (text) => {
      const digits = text.replace(/[\s-]/g, '');
      const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
      const data = await app.api.get('/v1/catalog/library', { ...query, limit: 12 });
      return data?.items || [];
    },
    describe: (item) => [item.title, [item.publisher, item.sectionCount ? `${num(item.sectionCount)} bölüm` : '',
      item.scope === 'tenant' ? 'kurum kaynağı' : ''].filter(Boolean).join(' · ')],
    onPick: (item) => {
      state.resourceId = item.id;
      state.title = item.title;
      state.sectionIds = [];
      paint();
      loadToc();
    },
  });
  node.append(picker.node, current, sections);

  function paint() {
    picker.node.hidden = Boolean(state.resourceId);
    current.replaceChildren();
    if (!state.resourceId) { sections.replaceChildren(); return; }
    current.append(h('b', '', state.title || 'Seçili kaynak'),
      button('Kaynağı değiştir', { variant: 'ghost', onClick: () => {
        state.resourceId = '';
        state.title = '';
        state.sectionIds = [];
        paint();
      } }));
  }

  async function loadToc() {
    const id = state.resourceId;
    sections.replaceChildren(h('span', 'ak-muted', 'İçindekiler yükleniyor…'));
    try {
      const data = await app.api.getCached(`/v1/catalog/library/${id}`, undefined, { ttlMs: 10 * 60_000 });
      if (id !== state.resourceId) return;
      if (!state.title) { state.title = data?.title || ''; paint(); }
      sections.replaceChildren();
      const toc = data?.toc || [];
      if (!toc.length) {
        sections.append(h('span', 'ak-muted', 'Bu kaynağın içindekiler listesi yok; sayfa aralığı yazabilirsiniz.'));
        return;
      }
      sections.append(h('span', 'kit-field-hint', 'Bölüm seçimi isteğe bağlı; en çok 20 bölüm.'));
      for (const unit of [...toc].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
        const group = h('div', 'ak-plan-sec-group');
        group.append(h('div', 'ak-plan-sec-title', unit.title));
        const chips = h('div', 'ak-chips');
        for (const section of [...(unit.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
          state.titles.set(section.id, section.title);
          const chip = h('button', 'kit-chip', section.questionCount
            ? `${section.title} (${num(section.questionCount)} soru)` : section.title);
          chip.type = 'button';
          const sync = () => {
            const on = state.sectionIds.includes(section.id);
            chip.classList.toggle('on', on);
            chip.setAttribute('aria-pressed', on ? 'true' : 'false');
          };
          chip.addEventListener('click', () => {
            if (state.sectionIds.includes(section.id)) {
              state.sectionIds = state.sectionIds.filter((item) => item !== section.id);
            } else if (state.sectionIds.length < 20) {
              state.sectionIds = [...state.sectionIds, section.id];
            }
            sync();
          });
          sync();
          chips.append(chip);
        }
        group.append(chips);
        sections.append(group);
      }
    } catch (failure) {
      if (id !== state.resourceId) return;
      sections.replaceChildren(h('span', 'ak-muted', problem(failure, 'İçindekiler alınamadı.')));
    }
  }

  paint();
  if (state.resourceId) loadToc();
  return {
    node,
    value: () => ({ resourceId: state.resourceId, title: state.title, sectionIds: [...state.sectionIds],
      sectionTitles: state.sectionIds.map((id) => state.titles.get(id)).filter(Boolean) }),
    destroy: picker.destroy,
  };
}

// ----------------------------------------------------------- görev düzenleyici

function buildEditor(app, { mode, task, duration, isNew, onSave, onCancel }) {
  const patch = mode === 'patch';
  const offset = mode === 'offset';
  const disposers = [];
  const node = h('div', 'ak-plan-editor');
  node.append(h('div', 'ak-plan-editor-head', isNew ? 'Yeni görev' : 'Görevi düzenle'));
  const grid = h('div', 'kit-form-grid');
  node.append(grid);
  const blocks = {};

  const block = (key, label, control, { wide = false, hint = '' } = {}) => {
    const wrap = h('div', `kit-field${wide ? ' kit-field-wide' : ''}`);
    const caption = h('span', 'kit-field-label', label);
    const star = h('span', 'req', '*');
    star.hidden = true;
    caption.append(star);
    wrap.append(caption, control);
    if (hint) wrap.append(h('span', 'kit-field-hint', hint));
    blocks[key] = { wrap, star };
    grid.append(wrap);
    return blocks[key];
  };
  const need = (key, on) => { if (blocks[key]) blocks[key].star.hidden = !on; };

  // Tür
  let type = task.type || 'topic_study';
  if (patch) {
    block('type', 'Görev', h('div', 'ak-plan-static', `${TASK_TYPES[type] || type} · ${taskTitle(task)}`),
      { wide: true, hint: 'Tür ve içerik değişmez; gerekirse atamayı geri alıp yenisini açın.' });
  } else {
    const select = h('select', 'kit-select');
    select.setAttribute('aria-label', 'Görev türü');
    for (const key of offset ? TEMPLATE_TYPES : Object.keys(TASK_TYPES)) {
      const item = h('option', '', TASK_TYPES[key]);
      item.value = key;
      select.append(item);
    }
    if (!select.querySelector(`option[value="${type}"]`)) type = 'topic_study';
    select.value = type;
    select.addEventListener('change', () => { type = select.value; paint(); });
    block('type', 'Tür', select);
    need('type', true);
  }

  // Gün
  let dayInput = null;
  let dateCtl = null;
  if (offset) {
    dayInput = numberInput(task.dayOffset !== undefined && task.dayOffset !== null ? Number(task.dayOffset) + 1 : 1,
      { label: 'Gün no' });
    block('when', 'Gün no', dayInput, { hint: duration ? `1–${duration} (1 = başlangıç günü)` : '1 = başlangıç günü' });
  } else {
    dateCtl = dateField({ value: task.date || '', label: 'Gün' });
    disposers.push(() => dateCtl.destroy());
    block('when', 'Gün', dateCtl.node);
  }
  need('when', true);

  const start = clockInput(task.startTime || '', null, 'Başlangıç saati');
  const end = clockInput(task.endTime || '', null, 'Bitiş saati');
  const clocks = h('div', 'ak-inline');
  clocks.append(start.node, h('span', 'ak-muted', '–'), end.node);
  block('clock', 'Saat aralığı (isteğe bağlı)', clocks);
  const minutes = numberInput(task.estimatedMinutes ?? 45, { label: 'Tahmini süre' });
  block('minutes', 'Tahmini süre (dk)', minutes, { hint: '5–600 dk. Saat aralığı yazıp boş bırakırsanız aralıktan hesaplanır.' });
  need('minutes', true);

  // İçerik
  let topic = null;
  let resource = null;
  let pageFrom = null;
  let pageTo = null;
  let readPages = null;
  let examSel = null;
  let homeworkSel = null;
  let mistakeCount = null;
  if (!patch) {
    topic = topicPicker(app, { subjectId: task.subjectId || task.mistakeReview?.subjectId || '',
      topicId: task.topicId || task.mistakeReview?.topicId || '', topicName: task._view?.topicName || '' });
    block('topic', 'Ders ve konu', topic.node, { wide: true,
      hint: 'Konu isteğe bağlıdır; programın ya da ünitenin tamamı da seçilebilir.' });
    resource = resourceField(app, { resourceId: task.resourceId || '', title: task._view?.resourceTitle || '',
      sectionIds: task.sectionIds || [] });
    disposers.push(resource.destroy);
    block('resource', 'Kaynak ve bölümler', resource.node, { wide: true });
    if (!offset) {
      pageFrom = numberInput(task.pageFrom ?? '', { placeholder: 'ilk', label: 'İlk sayfa' });
      pageTo = numberInput(task.pageTo ?? '', { placeholder: 'son', label: 'Son sayfa' });
      const pages = h('div', 'ak-inline');
      pages.append(pageFrom, h('span', 'ak-muted', '–'), pageTo);
      block('pages', 'Sayfa aralığı (isteğe bağlı)', pages);
    }
    readPages = numberInput(task.targetPages ?? '', { label: 'Hedef sayfa' });
    block('readPages', 'Hedef sayfa (isteğe bağlı)', readPages, { hint: '1–2.000' });
    if (!offset) {
      examSel = remoteSelect({
        load: () => app.api.getCached('/v1/admin/exams', { from: todayIst(-7), limit: 100 })
          .then((data) => data?.items || []),
        value: task.examId || '', empty: 'Deneme seçin', label: 'Deneme', missing: 'Kayıtlı deneme',
        describe: (exam) => `${exam.name} · ${fmtDate(exam.date)}`,
      });
      block('exam', 'Deneme', examSel.node, { wide: true, hint: 'Son bir hafta ve sonrasındaki denemeler.' });
      homeworkSel = remoteSelect({
        load: () => app.api.getCached('/v1/admin/homework', { state: 'active', limit: 100 })
          .then((data) => data?.items || []),
        value: task.homeworkId || '', empty: 'Ödev seçin', label: 'Ödev', missing: 'Kayıtlı ödev',
        describe: (item) => [item.title, item.subject?.shortName || item.subject?.name,
          item.dueAt ? `son ${fmtDate(item.dueAt)}` : ''].filter(Boolean).join(' · '),
      });
      block('homework', 'Ödev', homeworkSel.node, { wide: true, hint: 'Süresi dolmamış ödevler.' });
      mistakeCount = numberInput(task.mistakeReview?.count ?? 10, { label: 'Hata sayısı' });
      block('mistakeCount', 'Tekrar edilecek hata sayısı', mistakeCount, { hint: '1–50' });
    }
  }
  const questions = numberInput(task.targetQuestions ?? '', { label: 'Hedef soru' });
  block('questions', 'Hedef soru sayısı', questions, { hint: '1–1.000' });

  const title = h('input', 'kit-input');
  title.type = 'text';
  title.maxLength = 120;
  title.value = task.title || '';
  title.placeholder = patch ? '' : 'Boş bırakılırsa seçimden oluşturulur';
  block('title', 'Başlık', title, { wide: true });
  const note = h('textarea', 'kit-textarea');
  note.maxLength = 500;
  note.value = task.note || '';
  block('note', 'Not (isteğe bağlı)', note, { wide: true });

  const error = h('div', 'ak-plan-editor-error');
  const actions = h('div', 'ak-plan-editor-actions');
  actions.append(button('Vazgeç', { variant: 'ghost', onClick: () => onCancel() }),
    button(isNew ? 'Listeye ekle' : 'Görevi güncelle', { variant: 'primary', onClick: () => save() }));
  node.append(error, actions);

  function paint() {
    const spec = TYPE_SPEC[type] || { show: [], need: [] };
    for (const key of CONTENT_KEYS) {
      if (blocks[key]) blocks[key].wrap.hidden = !spec.show.includes(key);
    }
    need('topic', spec.need.includes('subject'));
    need('resource', spec.need.includes('resource'));
    need('questions', spec.need.includes('questions'));
    need('exam', spec.need.includes('exam'));
    need('homework', spec.need.includes('homework'));
    need('title', spec.need.includes('title'));
    if (spec.show.includes('exam')) examSel?.ensure();
    if (spec.show.includes('homework')) homeworkSel?.ensure();
  }

  function save() {
    const spec = TYPE_SPEC[type] || { show: [], need: [] };
    const show = new Set(spec.show);
    const required = new Set(spec.need);
    const problems = [];
    const out = patch ? { ...task } : { type };
    const view = { ...(task._view || {}) };

    if (offset) {
      const day = intOf(dayInput.value);
      const limit = duration || 60;
      if (!day || day < 1 || day > limit) problems.push(`Gün no 1–${limit} arasında olmalı.`);
      else out.dayOffset = day - 1;
    } else {
      const iso = dateCtl.get();
      if (!iso) problems.push('Gün seçin.');
      else if (iso < todayIst() && (!patch || iso !== task.date)) problems.push('Geçmiş bir güne görev konamaz.');
      out.date = iso;
    }

    const from = start.value;
    const to = end.value;
    if (from && !CLOCK.test(from)) problems.push('Başlangıç saatini 09:30 gibi yazın.');
    if (to && !CLOCK.test(to)) problems.push('Bitiş saatini 10:15 gibi yazın.');
    if (to && !from) problems.push('Bitiş saati için başlangıç saati de yazın.');
    if (from && to && CLOCK.test(from) && CLOCK.test(to) && to <= from) problems.push('Bitiş saati başlangıçtan sonra olmalı.');
    out.startTime = from || null;
    out.endTime = to || null;

    let length = intOf(minutes.value);
    if (length === null && from && to && CLOCK.test(from) && CLOCK.test(to) && to > from) {
      const toMinutes = (clock) => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
      length = toMinutes(to) - toMinutes(from);
    }
    if (!length || length < 5 || length > 600) problems.push('Tahmini süre 5–600 dakika olmalı.');
    out.estimatedMinutes = length;

    if (!patch) {
      Object.assign(out, { subjectId: null, topicId: null, resourceId: null, sectionIds: [],
        targetQuestions: null, targetPages: null });
      if (!offset) Object.assign(out, { pageFrom: null, pageTo: null, examId: null, homeworkId: null, mistakeReview: null });
      if (show.has('topic')) {
        const picked = topic.value();
        out.subjectId = picked.subjectId || null;
        out.topicId = picked.topicId || null;
        view.subjectName = picked.subjectName;
        view.topicName = picked.topicName;
        if (required.has('subject') && !picked.subjectId) problems.push('Ders seçin.');
      }
      if (show.has('resource')) {
        const picked = resource.value();
        if (required.has('resource') && !picked.resourceId) problems.push('Kaynak seçin.');
        out.resourceId = picked.resourceId || null;
        out.sectionIds = picked.resourceId ? picked.sectionIds : [];
        view.resourceTitle = picked.title;
        view.sectionTitles = picked.sectionTitles;
      }
      if (show.has('pages') && !offset) {
        const first = intOf(pageFrom.value);
        const last = intOf(pageTo.value);
        if (Number.isNaN(first) || Number.isNaN(last) || first === 0 || last === 0) problems.push('Sayfa numarası 1 ya da büyük olmalı.');
        else if (first && last && last < first) problems.push('Son sayfa ilk sayfadan küçük olamaz.');
        out.pageFrom = first || null;
        out.pageTo = last || null;
      }
      if (show.has('readPages')) {
        const pages = intOf(readPages.value);
        if (pages !== null && (!pages || pages > 2000)) problems.push('Hedef sayfa 1–2.000 olmalı.');
        out.targetPages = pages || null;
      }
      if (show.has('exam') && examSel) {
        const id = examSel.value();
        if (!id) problems.push('Deneme seçin.');
        out.examId = id || null;
        view.examName = examSel.item()?.name || view.examName || '';
      }
      if (show.has('homework') && homeworkSel) {
        const id = homeworkSel.value();
        if (!id) problems.push('Ödev seçin.');
        out.homeworkId = id || null;
        view.homeworkTitle = homeworkSel.item()?.title || view.homeworkTitle || '';
      }
      if (show.has('mistakeCount') && mistakeCount) {
        const count = intOf(mistakeCount.value) ?? 10;
        if (!count || count > 50) problems.push('Hata sayısı 1–50 olmalı.');
        out.mistakeReview = { subjectId: out.subjectId, topicId: out.topicId, count };
      }
    }
    if (show.has('questions')) {
      const count = intOf(questions.value);
      if (count === null && required.has('questions')) problems.push('Hedef soru sayısını yazın.');
      else if (count !== null && (!count || count > 1000)) problems.push('Hedef soru 1–1.000 olmalı.');
      out.targetQuestions = count || null;
    }

    const text = title.value.trim();
    if (required.has('title') && !text) problems.push('Serbest görev için başlık yazın.');
    out.title = text || (patch ? null : autoTitle(type, out, view));
    out.note = note.value.trim() || null;
    out._view = view;

    if (problems.length) { error.textContent = problems.join(' '); return; }
    onSave(out);
  }

  paint();
  return { node, destroy: () => disposers.forEach((fn) => fn()) };
}

// ----------------------------------------------------------- görev listesi alanı

/**
 * formDialog için görev listesi alanı (`type: 'custom'`): üstte eklenen
 * görevler, altta tek bir düzenleyici. `onChange(tasks)` her değişiklikte
 * tam listeyi verir. formDialog `update(draft)` çağırınca şablon süresi
 * (`durationDays`) okunur ve süre dışı kalan görevler işaretlenir.
 */
export function taskListField(app, { mode = 'date', tasks = [], durationDays = null, defaultDate = todayIst(1),
  onChange } = {}) {
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

  function open(index, seed = null) {
    close();
    editing = index;
    const last = items[items.length - 1];
    const task = index !== null ? items[index]
      : seed || { type: last?.type || 'topic_study', estimatedMinutes: last?.estimatedMinutes || 45,
        date: last?.date || defaultDate, dayOffset: last ? Number(last.dayOffset || 0) : 0 };
    editor = buildEditor(app, { mode, task, duration, isNew: index === null,
      onSave: (next) => {
        if (editing === null) items.push(next);
        else items[editing] = next;
        items = sortTasks(items);
        close();
        paint();
        fire();
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
