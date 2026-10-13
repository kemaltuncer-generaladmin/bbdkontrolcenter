// Sınıflar — SINIF SEVİYEYİ TAŞIR (kullanıcı kararı 13.10.2026, göç 008).
//
// "Sınıf ve seviye farkı garip, bunları teke indiremez miyiz?" → seviye
// (5–12 / mezun), alan (SAY/EA/SÖZ/DİL; 10. sınıf ve üstü, mezun) ve hedef
// sınav (LGS/YKS) ANA SINIF tanımında girilir; öğrenci yalnız sınıfa atanır,
// seviyesi ve müfredatı (BBD Akademi) sınıftan türetilir. Seviyesi girilmemiş
// sınıf "Seviye seçin" ile işaretlenir; addan gelen öneri tek tıkla doldurulur
// (yazılmaz — Kaydet ile yazılır).
//
//   GET    /api/bbd_students/classes            bbd_students.view
//   PUT    /api/bbd_students/classes/{ad}       bbd_students.manage
//   DELETE /api/bbd_students/classes/{ad}       bbd_students.manage
//
// Kayıtta sınıftaki her öğrencinin geçerli değeri değişirse öğrenci başına
// `student.updated` çıkar; Akademi müfredatı kendiliğinden yeniden bağlar.
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { confirmSimple } from '../../ui-kit/kit.js';
import {
  EXAM_CHOICES, GRADE_CHOICES, TRACK_CHOICES, classDraft, classPatch, describe, parseChoice, parseGrade,
  suggestedExam, trackAllowed,
} from './curriculum.js';
import { normalizeClass } from './student.js';

const BASE = '/api/bbd_students/classes';

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function button(label, variant, onClick) {
  const node = h('button', `st-btn${variant ? ` st-btn-${variant}` : ''}`, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

/**
 * "Sınıflar" sekmesi.
 * @param {HTMLElement} host
 * @param {{api, can:(p:string)=>boolean, toast:(t:string)=>void, dialogRoot:HTMLElement,
 *          onChanged?:()=>void, toBulk?:()=>void}} ctx
 */
export function mountClasses(host, ctx) {
  const canEdit = ctx.can('bbd_students.manage');
  let items = [];
  let uidCounter = 0;
  const view = h('div', 'cl');
  const intro = h('p', 'st-hint', 'Seviye, alan ve hedef sınav SINIFTA tanımlanır; öğrenci yalnız sınıfa '
    + 'alınır ve müfredatı (BBD Akademi) sınıfından gelir. Önce sınıflara seviye verin, sonra öğrencileri '
    + 'sınıflara alın (Öğrenciler → seç → “Ana sınıf”).');
  const summary = h('p', 'st-status');
  summary.setAttribute('role', 'status');
  summary.setAttribute('aria-live', 'polite');
  const table = h('div', 'cl-table');
  table.setAttribute('role', 'table');
  table.setAttribute('aria-label', 'Sınıflar ve seviyeleri');
  const addBox = h('div', 'cl-add');
  view.append(intro, summary, table, addBox);
  host.replaceChildren(view);

  async function reload() {
    summary.textContent = 'Sınıflar okunuyor…';
    try {
      const data = await ctx.api(BASE);
      items = data?.items || [];
      const missing = data?.undefinedCount || 0;
      summary.textContent = items.length === 0 ? 'Henüz sınıf yok. Aşağıdan sınıf tanımlayın.'
        : missing ? `${missing} sınıfa seviye girilmemiş — bu sınıflardaki öğrencilerin müfredatı bağlanamıyor.`
          : `${items.length} sınıf · hepsinin seviyesi tanımlı.`;
      summary.classList.toggle('bad', Boolean(missing));
      paint();
    } catch (failure) {
      summary.textContent = `Sınıflar okunamadı: ${failure.message}`;
      summary.classList.add('bad');
    }
  }

  function selectField(label, choices, value, onChange, disabled = false) {
    uidCounter += 1;
    const id = `cl-${uidCounter}`;
    const wrap = h('div', 'st-field cl-field');
    const caption = h('label', 'st-label', label);
    caption.htmlFor = id;
    const select = h('select', 'st-input st-select');
    select.id = id;
    for (const [v, text] of choices) select.append(new Option(text, v));
    select.value = value === null || value === undefined ? '' : String(value);
    select.disabled = disabled || !canEdit;
    select.addEventListener('change', () => onChange(select.value));
    wrap.append(caption, select);
    return { wrap, select };
  }

  function row(item) {
    const node = h('div', `cl-row${item.defined ? '' : ' cl-row-missing'}`);
    node.setAttribute('role', 'row');
    const head = h('div', 'cl-name');
    head.setAttribute('role', 'rowheader');
    head.append(h('b', null, item.name), h('span', 'st-subtitle', `${item.studentCount} öğrenci`));
    if (!item.defined) head.append(h('span', 'st-badge st-badge-warn', 'Seviye seçin'));
    const draft = classDraft(item);
    const status = h('span', 'cl-status');
    const save = button(item.defined ? 'Kaydet' : 'Seviyeyi kaydet', 'primary', () => commit());
    save.disabled = !canEdit || !item.defined;
    const dirty = () => {
      const same = item.defined && draft.gradeLevel === item.gradeLevel && (draft.track ?? null) === (item.track ?? null)
        && (draft.examTarget ?? null) === (item.examTarget ?? null);
      save.disabled = !canEdit || same || draft.gradeLevel === null || draft.gradeLevel === undefined;
    };
    const grade = selectField('Seviye', GRADE_CHOICES, draft.gradeLevel, (value) => {
      draft.gradeLevel = parseGrade(value);
      track.select.disabled = !canEdit || !trackAllowed(draft.gradeLevel);
      if (track.select.disabled) { draft.track = null; track.select.value = ''; }
      if (!draft.examTarget && suggestedExam(draft.gradeLevel)) {
        draft.examTarget = suggestedExam(draft.gradeLevel);
        exam.select.value = draft.examTarget;
      }
      dirty();
    });
    const track = selectField('Alan', TRACK_CHOICES, draft.track, (value) => { draft.track = parseChoice(value); dirty(); },
      !trackAllowed(draft.gradeLevel));
    const exam = selectField('Hedef sınav', EXAM_CHOICES, draft.examTarget, (value) => {
      draft.examTarget = parseChoice(value);
      dirty();
    });
    for (const field of [grade, track, exam]) field.wrap.setAttribute('role', 'cell');
    if (!item.defined && item.suggestion) {
      status.textContent = `Addan öneri: ${describe(item.suggestion)} — kontrol edip kaydedin.`;
    }
    dirty();

    async function commit() {
      const patch = classPatch(draft);
      if (patch.error) { status.textContent = patch.error; return; }
      save.disabled = true;
      status.textContent = 'Kaydediliyor…';
      try {
        const result = await ctx.api(`${BASE}/${encodeURIComponent(item.name)}`, { method: 'PUT', body: patch.body });
        ctx.toast?.(`“${item.name}”: ${result.label || 'kaydedildi'} — ${result.updatedStudents || 0} öğrenci güncellendi.`);
        ctx.onChanged?.();
        await reload();
      } catch (failure) {
        status.textContent = `Kaydedilemedi: ${failure.message}`;
        dirty();
      }
    }

    const tools = h('div', 'cl-tools');
    tools.setAttribute('role', 'cell');
    tools.append(save);
    if (item.defined && canEdit) {
      tools.append(button('Tanımı kaldır', '', async () => {
        if (!await confirmSimple(ctx.dialogRoot || view, { title: `“${item.name}” seviyesi kaldırılsın mı?`,
          description: 'Sınıftaki öğrencilerin müfredatı bağlanamaz (eski öğrenci kaydı varsa o geçerli olur).',
          confirmLabel: 'Kaldır' })) return;
        try {
          await ctx.api(`${BASE}/${encodeURIComponent(item.name)}`, { method: 'DELETE' });
          ctx.onChanged?.();
          await reload();
        } catch (failure) {
          status.textContent = `Kaldırılamadı: ${failure.message}`;
        }
      }));
    }
    tools.append(status);
    node.append(head, grade.wrap, track.wrap, exam.wrap, tools);
    return node;
  }

  function paint() {
    table.replaceChildren(...items.map(row));
    addBox.replaceChildren();
    if (!canEdit) return;
    // Yeni sınıf: ad + seviye. Öğrenciler sonra "Ana sınıf" ile alınır.
    addBox.append(h('p', 'st-subhead', 'Yeni sınıf tanımla'));
    const nameId = 'cl-new-name';
    const nameWrap = h('div', 'st-field cl-field');
    const caption = h('label', 'st-label', 'Sınıf adı');
    caption.htmlFor = nameId;
    const name = h('input', 'st-input');
    name.id = nameId;
    name.placeholder = '11-A, 12-SAY, Mezun';
    name.maxLength = 20;
    nameWrap.append(caption, name);
    const item = { name: '', defined: false, studentCount: 0, gradeLevel: null, track: null, examTarget: null };
    const draft = classDraft(item);
    const grade = selectField('Seviye', GRADE_CHOICES, null, (value) => {
      draft.gradeLevel = parseGrade(value);
      track.select.disabled = !trackAllowed(draft.gradeLevel);
      if (!draft.examTarget && suggestedExam(draft.gradeLevel)) {
        draft.examTarget = suggestedExam(draft.gradeLevel);
        exam.select.value = draft.examTarget;
      }
    });
    const track = selectField('Alan', TRACK_CHOICES, null, (value) => { draft.track = parseChoice(value); });
    const exam = selectField('Hedef sınav', EXAM_CHOICES, null, (value) => { draft.examTarget = parseChoice(value); });
    const status = h('span', 'cl-status');
    const create = button('Sınıfı tanımla', 'primary', async () => {
      const clean = normalizeClass(name.value);
      if (!clean) { status.textContent = 'Sınıf adı yazın.'; return; }
      const patch = classPatch(draft);
      if (patch.error) { status.textContent = patch.error; return; }
      try {
        await ctx.api(`${BASE}/${encodeURIComponent(clean)}`, { method: 'PUT', body: patch.body });
        ctx.toast?.(`“${clean}” tanımlandı. Öğrencileri Öğrenciler → seç → “Ana sınıf” ile alın.`);
        ctx.onChanged?.();
        await reload();
      } catch (failure) {
        status.textContent = `Tanımlanamadı: ${failure.message}`;
      }
    });
    const grid = h('div', 'cl-row cl-row-new');
    grid.append(nameWrap, grade.wrap, track.wrap, exam.wrap, create, status);
    addBox.append(grid);
    if (ctx.toBulk) addBox.append(button('Öğrencileri sınıfa al →', '', () => ctx.toBulk()));
  }

  reload();
  return { reload, node: view };
}
