// BBD Akademi — Kaynaklar › İçindekiler düzenleyicisi (F-C4).
//
// Kurum kaynağının içindekileri: ünite → bölüm (test / konu anlatımı …),
// sayfa aralığı, soru sayısı ve müfredat konusu eşlemesi. Kayıt TEK İSTEKTİR
// (`admin_putResourceToc`, bütün ağaç); var olan ünite/bölüm kimliği gövdede
// geri gönderilir ki öğrencinin o bölümdeki ilerlemesi kaybolmasın. İlerlemesi
// olan bölüm silinmek istenirse Akademi `409 sections_with_progress` döner;
// kullanıcı onaylarsa aynı gövde `archiveRemovedWithProgress: true` ile yeniden
// gönderilir (bölüm arşivlenir, geçmişte kalır).
//
// Konular müfredattan gelir: program listesi ve program ayrıntısı (ünite →
// konu) salt okunur ve önbelleklidir (Müfredat bölümünün uçları; burada yalnız
// seçim için okunur). "Konulardan bölüm üret" önce `dryRun` ile önizler, onaydan
// sonra Akademi üretip KAYDEDER.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, skeletonRows } from '../../../ui-kit/layout.js';
import { createPicker } from '../../../ui-kit/picker.js';
import { AkademiError } from '../api.js';
import { programLabel, sortPrograms } from '../shared/curriculum-picker.js';
import {
  actionButton, confirmAction, empty, errorBox, formDialog, multiChips, num, optionsOf, problem,
  toolbar,
} from '../common.js';
import {
  KINDS, SECTION_KINDS, levelText, resourcePicker, scopeBadge, subjectsText, tocPreview,
} from './resources-common.js';

const PROGRAMS_TTL = 10 * 60_000;
const byOrder = (a, b) => (a.order ?? 0) - (b.order ?? 0);
const isInt = (value, min) => Number.isInteger(value) && value >= min;

export function tocTab(app, ctx) {
  const node = h('div', 'ak-stack ak-res-toc');
  let resource = null;            // sunucudan son okunan kaynak (geri al buna döner)
  let units = [];                 // düzenlenen ağaç
  let baseline = '';
  let seq = 0;
  let saving = false;
  let opening = 0;
  const inputs = new Map();       // `${key}:${alan}` → giriş kutusu (hata işareti için)
  const topicNames = new Map();   // konu kimliği → ad
  const topicProgram = new Map(); // konu kimliği → program kimliği
  const memory = { programId: '' };

  const picker = resourcePicker(app, { scope: 'tenant', placeholder: 'Kurum kaynağı ara (ad, yayınevi, ISBN)',
    onPick: (item) => open(item.id) });
  const head = h('div', 'ak-stack');
  const problems = h('div');
  const editor = h('div', 'ak-stack');
  node.append(toolbar(picker.node, h('span', 'ak-muted',
    'Hazır kütüphane kaynakları salt okunurdur; düzenlemek için önce kuruma kopyalayın.')), head, problems, editor);
  editor.append(empty('Bir kurum kaynağı seçin',
    'Yukarıdan arayın ya da Kurum kaynakları listesinde “İçindekiler”e basın.'));

  const editable = () => resource?.scope === 'tenant' && app.can('admin_putResourceToc');
  const isDirty = () => Boolean(resource) && editable() && serialize() !== baseline;

  // ----------------------------------------------------------- yükleme
  async function open(resourceId, { force = false, data = null } = {}) {
    // Aynı kaynak yeniden istenirse (ör. listeden tekrar "İçindekiler") düzenleme korunur.
    if (!force && resource?.id === resourceId && isDirty()) return;
    if (!force && resource && resource.id !== resourceId && isDirty()) {
      const yes = await confirmAction(app, { title: 'Kaydedilmemiş değişiklikler atılsın mı?',
        description: `“${resource.title}” içindekilerinde kaydedilmemiş değişiklik var.`,
        confirmLabel: 'Değişiklikleri at', danger: true });
      if (!yes) return;
    }
    const mine = ++opening;
    if (data) { load(data); return; }
    head.replaceChildren();
    problems.replaceChildren();
    editor.replaceChildren(skeletonRows(8, 6));
    try {
      const data = await app.api.get(`/v1/catalog/library/${resourceId}`);
      if (mine !== opening) return;
      load(data);
    } catch (failure) {
      if (mine !== opening) return;
      editor.replaceChildren(errorBox(failure, () => open(resourceId, { force: true })));
    }
  }

  function load(data) {
    resource = data;
    for (const row of data.topicCoverage || []) {
      if (!row.topic) continue;
      topicNames.set(row.topic.id, row.topic.name);
      if (row.topic.programId) topicProgram.set(row.topic.id, row.topic.programId);
    }
    units = [...(data.toc || [])].sort(byOrder).map((unit) => ({
      key: ++seq,
      id: unit.id,
      title: unit.title || '',
      sections: [...(unit.sections || [])].sort(byOrder).map((section) => ({
        key: ++seq,
        id: section.id,
        title: section.title || '',
        kind: section.kind || 'test',
        pageFrom: section.pageFrom ?? null,
        pageTo: section.pageTo ?? null,
        questionCount: section.questionCount ?? 0,
        topicIds: [...(section.topicIds || [])],
      })),
    }));
    baseline = serialize();
    problems.replaceChildren();
    paint();
  }

  function payload() {
    return units.map((unit) => ({
      id: unit.id || null,
      title: unit.title.trim(),
      sections: unit.sections.map((section) => ({
        id: section.id || null,
        title: section.title.trim(),
        kind: section.kind,
        pageFrom: section.pageFrom ?? null,
        pageTo: section.pageTo ?? null,
        questionCount: section.questionCount ?? 0,
        topicIds: [...section.topicIds],
      })),
    }));
  }
  function serialize() { return JSON.stringify(payload()); }

  // ------------------------------------------------------------- çizim
  const summary = h('span', 'ak-res-toc-summary');
  const dirtyBadge = badge('Kaydedilmemiş değişiklik', 'warn');
  let saveButton = null;
  let revertButton = null;

  function paint() {
    paintHead();
    inputs.clear();
    editor.replaceChildren();
    if (!editable()) {
      if (resource.scope === 'tenant') {
        editor.append(alertBox('İçindekileri değiştirme yetkiniz yok; salt okunur görünüm.', 'info'));
      }
      editor.append(tocPreview(resource));
      return;
    }
    if (!units.length) {
      editor.append(empty('İçindekiler boş', 'Ünite ekleyin ya da “Konulardan bölüm üret” ile müfredattan hızlıca oluşturun.'));
    }
    units.forEach((unit, index) => editor.append(unitCard(unit, index)));
    if (units.length) {
      editor.append(toolbar(button('+ Ünite ekle', { onClick: () => addUnit() })));
    }
    paintSummary();
  }

  function paintHead() {
    const title = h('div', 'ak-res-toc-title');
    title.append(h('b', '', resource.title), scopeBadge(resource.scope),
      h('span', 'ak-muted', [KINDS[resource.kind], resource.publisher, levelText(resource), subjectsText(resource)]
        .filter((part) => part && part !== '—').join(' · ')));
    const tools = toolbar(button('Ayrıntı', { variant: 'ghost', onClick: () => ctx.openResource(resource.id) }));
    if (resource.scope === 'global') {
      tools.append(actionButton(app, 'admin_createResource', 'Kuruma kopyala', { variant: 'primary',
        onClick: () => ctx.copyToTenant(resource) }));
      head.replaceChildren(title, tools, alertBox('Bu kaynak hazır kütüphanededir ve salt okunurdur. '
        + 'Kurumunuza göre değiştirmek için kopyasını alın; kopya burada düzenlenir.', 'info'));
      return;
    }
    if (editable()) {
      saveButton = actionButton(app, 'admin_putResourceToc', 'Kaydet', { variant: 'primary', onClick: () => save() });
      revertButton = button('Geri al', { variant: 'ghost', onClick: () => revert() });
      tools.append(
        button('+ Ünite ekle', { onClick: () => addUnit() }),
        actionButton(app, 'admin_generateResourceToc', 'Konulardan bölüm üret', { onClick: () => generate() }),
        h('span', 'kit-spacer'), summary, dirtyBadge, revertButton, saveButton,
      );
    }
    head.replaceChildren(title, tools);
  }

  function paintSummary() {
    const sections = units.flatMap((unit) => unit.sections);
    const questions = sections.reduce((acc, row) => acc + (Number.isInteger(row.questionCount) ? row.questionCount : 0), 0);
    const mapped = sections.filter((row) => row.topicIds.length).length;
    summary.textContent = `${num(units.length)} ünite · ${num(sections.length)} bölüm · ${num(questions)} soru · `
      + `${num(mapped)}/${num(sections.length)} bölüm konu eşli`;
    const dirty = isDirty();
    dirtyBadge.hidden = !dirty;
    if (saveButton && !saveButton.dataset.blocked) saveButton.disabled = saving || !dirty;
    if (revertButton) revertButton.disabled = saving || !dirty;
  }

  function touched() {
    paintSummary();
  }

  function unitCard(unit, index) {
    const box = h('section', 'ak-res-unit');
    const top = h('div', 'ak-res-unit-head');
    const questions = unit.sections.reduce((acc, row) => acc + (Number.isInteger(row.questionCount) ? row.questionCount : 0), 0);
    const meta = h('span', 'ak-muted', `${num(unit.sections.length)} bölüm · ${num(questions)} soru`);
    const title = textInput(unit.title, { placeholder: 'Ünite adı', maxLength: 120, label: `${index + 1}. ünite adı`,
      className: 'ak-res-unit-title', onInput: (value) => { unit.title = value; touched(); } });
    inputs.set(`${unit.key}:title`, title);
    top.append(h('span', 'ak-res-unit-no', `${index + 1}.`), title, meta, h('span', 'kit-spacer'),
      iconButton('↑', 'Üniteyi yukarı taşı', () => move(units, index, -1), index === 0),
      iconButton('↓', 'Üniteyi aşağı taşı', () => move(units, index, 1), index === units.length - 1),
      button('+ Bölüm', { variant: 'ghost', onClick: () => addSection(unit) }),
      button('Üniteyi sil', { variant: 'ghost', onClick: () => removeUnit(unit, index) }));
    box.append(top);
    if (unit.sections.length) {
      const header = h('div', 'ak-res-sec ak-res-sec-head');
      for (const label of ['#', 'Bölüm adı', 'Tür', 'Sayfa', '', 'Soru', 'Konular', '']) header.append(h('span', '', label));
      box.append(header);
      unit.sections.forEach((section, sectionIndex) => box.append(sectionRow(unit, section, sectionIndex)));
    } else {
      box.append(h('div', 'ak-muted ak-res-unit-empty', 'Bu ünitede bölüm yok. “+ Bölüm” ile ekleyin.'));
    }
    return box;
  }

  function sectionRow(unit, section, index) {
    const row = h('div', 'ak-res-sec');
    const title = textInput(section.title, { placeholder: 'Test 1 — Konu adı', maxLength: 120, label: 'Bölüm adı',
      onInput: (value) => { section.title = value; touched(); } });
    title.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); addSection(unit, index + 1); }
    });
    const kind = h('select', 'kit-select');
    kind.setAttribute('aria-label', 'Bölüm türü');
    for (const [value, label] of Object.entries(SECTION_KINDS)) {
      const option = h('option', '', label);
      option.value = value;
      kind.append(option);
    }
    kind.value = section.kind;
    kind.addEventListener('change', () => { section.kind = kind.value; touched(); });
    const pageFrom = intInput(section.pageFrom, { label: 'İlk sayfa', placeholder: 'ilk',
      onInput: (value) => { section.pageFrom = value; touched(); } });
    const pageTo = intInput(section.pageTo, { label: 'Son sayfa', placeholder: 'son',
      onInput: (value) => { section.pageTo = value; touched(); } });
    const questions = intInput(section.questionCount, { label: 'Soru sayısı', placeholder: '0', required: true,
      onInput: (value) => { section.questionCount = value ?? 0; touched(); } });
    for (const [field, input] of [['title', title], ['pageFrom', pageFrom], ['pageTo', pageTo], ['questionCount', questions]]) {
      inputs.set(`${section.key}:${field}`, input);
    }
    let topics = topicCell(section);
    const refreshTopics = () => { const next = topicCell(section); topics.replaceWith(next); topics = next; };
    function topicCell(item) {
      const cell = h('div', 'ak-res-topics');
      const names = item.topicIds.map((id) => topicNames.get(id) || id);
      for (const name of names.slice(0, 2)) cell.append(h('span', 'ak-res-topic', name));
      if (names.length > 2) cell.append(h('span', 'ak-res-topic more', `+${names.length - 2}`));
      cell.title = names.join(', ');
      cell.append(button(names.length ? 'Değiştir' : 'Konu seç', { variant: 'ghost', onClick: async () => {
        const ids = await topicDialog(app, { resource, selected: item.topicIds, topicNames, topicProgram, memory,
          title: item.title.trim() || 'Bölüm' });
        if (!ids) return;
        item.topicIds = ids;
        refreshTopics();
        touched();
      } }));
      return cell;
    }
    const actions = h('div', 'ak-res-sec-actions');
    actions.append(
      iconButton('↑', 'Yukarı taşı', () => move(unit.sections, index, -1), index === 0),
      iconButton('↓', 'Aşağı taşı', () => move(unit.sections, index, 1), index === unit.sections.length - 1),
      iconButton('⧉', 'Çoğalt (altına kopyası)', () => duplicate(unit, index)),
      iconButton('✕', 'Bölümü sil', () => { unit.sections.splice(index, 1); paint(); }),
    );
    row.append(h('span', 'ak-res-sec-no', String(index + 1)), title, kind, pageFrom, pageTo, questions, topics, actions);
    return row;
  }

  // ----------------------------------------------------------- düzenleme
  function move(list, index, step) {
    const target = index + step;
    if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    paint();
  }

  function addUnit() {
    units.push({ key: ++seq, id: null, title: `Ünite ${units.length + 1}`, sections: [] });
    paint();
    focus(`${units[units.length - 1].key}:title`);
  }

  /** Yeni bölüm: önceki bölümün türü, soru sayısı ve sayfa sırası devralınır (hızlı giriş). */
  function addSection(unit, at = unit.sections.length) {
    const previous = unit.sections[at - 1] || unit.sections[unit.sections.length - 1] || null;
    const nextPage = previous && Number.isInteger(previous.pageTo) ? previous.pageTo + 1 : null;
    const span = previous && Number.isInteger(previous.pageFrom) && Number.isInteger(previous.pageTo)
      ? previous.pageTo - previous.pageFrom : null;
    const section = {
      key: ++seq,
      id: null,
      title: `Test ${unit.sections.length + 1}`,
      kind: previous?.kind || 'test',
      pageFrom: nextPage,
      pageTo: nextPage && span !== null ? nextPage + span : null,
      questionCount: previous && Number.isInteger(previous.questionCount) ? previous.questionCount : 0,
      topicIds: [],
    };
    unit.sections.splice(at, 0, section);
    paint();
    focus(`${section.key}:title`, true);
  }

  function duplicate(unit, index) {
    const source = unit.sections[index];
    unit.sections.splice(index + 1, 0, { ...source, key: ++seq, id: null, topicIds: [...source.topicIds],
      title: `${source.title} (kopya)` });
    paint();
  }

  async function removeUnit(unit, index) {
    if (unit.sections.length) {
      const yes = await confirmAction(app, { title: `“${unit.title || 'Ünite'}” silinsin mi?`,
        description: `Ünite ve içindeki ${num(unit.sections.length)} bölüm düzenleyiciden kalkar. `
          + 'Kaydedene kadar “Geri al” ile dönebilirsiniz.', confirmLabel: 'Sil', danger: true });
      if (!yes) return;
    }
    units.splice(index, 1);
    paint();
  }

  function focus(id, select = false) {
    const input = inputs.get(id);
    if (!input) return;
    input.focus();
    if (select) input.select();
  }

  async function revert() {
    const yes = await confirmAction(app, { title: 'Değişiklikler geri alınsın mı?',
      description: 'Düzenleyici son kaydedilen içindekilere döner.', confirmLabel: 'Geri al', danger: true });
    if (yes) load(resource);
  }

  // ---------------------------------------------------------------- kayıt
  function validate() {
    const found = [];
    for (const input of inputs.values()) input.classList.remove('bad');
    const mark = (key, field, message) => {
      inputs.get(`${key}:${field}`)?.classList.add('bad');
      found.push(message);
    };
    units.forEach((unit, unitIndex) => {
      if (!unit.title.trim()) mark(unit.key, 'title', `${unitIndex + 1}. ünitenin adı boş.`);
      unit.sections.forEach((section, sectionIndex) => {
        const label = `${unitIndex + 1}.${sectionIndex + 1}`;
        if (!section.title.trim()) mark(section.key, 'title', `${label} bölümünün adı boş.`);
        if (!isInt(section.questionCount, 0)) mark(section.key, 'questionCount', `${label}: soru sayısı 0 ya da daha büyük tam sayı olmalı.`);
        if (section.pageFrom !== null && !isInt(section.pageFrom, 1)) mark(section.key, 'pageFrom', `${label}: ilk sayfa 1 ya da daha büyük olmalı.`);
        if (section.pageTo !== null && !isInt(section.pageTo, 1)) mark(section.key, 'pageTo', `${label}: son sayfa 1 ya da daha büyük olmalı.`);
        if (isInt(section.pageFrom, 1) && isInt(section.pageTo, 1) && section.pageTo < section.pageFrom) {
          mark(section.key, 'pageTo', `${label}: son sayfa ilk sayfadan küçük olamaz.`);
        }
      });
    });
    return found;
  }

  async function save(archive = false) {
    if (saving) return;
    const found = validate();
    if (found.length) {
      problems.replaceChildren(alertBox(`${num(found.length)} alan düzeltilmeli: ${found.slice(0, 4).join(' ')}`
        + `${found.length > 4 ? ' …' : ''}`, 'bad'));
      editor.querySelector('.bad')?.focus();
      return;
    }
    problems.replaceChildren();
    saving = true;
    paintSummary();
    if (saveButton) saveButton.textContent = 'Kaydediliyor…';
    try {
      const next = await app.api.put(`/v1/admin/resources/${resource.id}/toc`,
        { units: payload(), archiveRemovedWithProgress: archive });
      saving = false;
      app.toast(archive ? 'İçindekiler kaydedildi; ilerlemesi olan bölümler arşivlendi.' : 'İçindekiler kaydedildi.', 'good');
      load(next);
      ctx.changed('resources');
    } catch (failure) {
      saving = false;
      if (saveButton) saveButton.textContent = 'Kaydet';
      paintSummary();
      if (failure instanceof AkademiError && failure.code === 'sections_with_progress' && !archive) {
        const count = Array.isArray(failure.details) ? failure.details.length : 0;
        const yes = await confirmAction(app, {
          title: 'Silinen bölümlerde öğrenci ilerlemesi var',
          description: `${count ? `${num(count)} bölümde` : 'Bazı bölümlerde'} öğrencilerin çözüm kaydı var. Bu bölümler `
            + 'silinmez, arşivlenir: öğrencinin geçmişinde ve raporlarda kalır, içindekilerde görünmez.',
          confirmLabel: 'Arşivleyerek kaydet',
          danger: true,
        });
        if (yes) await save(true);
        return;
      }
      problems.replaceChildren(alertBox(problem(failure, 'İçindekiler kaydedilemedi.'), 'bad'));
    }
  }

  async function generate() {
    const saved = await generateDialog(app, { resource, dirty: isDirty(), memory });
    if (!saved) return;
    const added = (saved.sectionCount || 0) - (resource.sectionCount || 0);
    app.toast(added > 0 ? `${num(added)} bölüm üretildi ve kaydedildi.` : 'İçindekiler üretildi ve kaydedildi.', 'good');
    load(saved);
    ctx.changed('resources');
  }

  return {
    node,
    open: (resourceId, data = null) => open(resourceId, { data }),
    destroy() { picker.destroy(); },
  };
}

// --------------------------------------------------------------- girişler

function textInput(value, { placeholder = '', maxLength = 0, label = '', className = '', onInput }) {
  const input = h('input', `kit-input${className ? ` ${className}` : ''}`);
  input.type = 'text';
  input.value = value || '';
  if (placeholder) input.placeholder = placeholder;
  if (maxLength) input.maxLength = maxLength;
  if (label) input.setAttribute('aria-label', label);
  input.addEventListener('input', () => { input.classList.remove('bad'); onInput(input.value); });
  return input;
}

/** Tam sayı kutusu: boş → null; geçersiz yazım NaN olarak saklanır ve kayıtta işaretlenir. */
function intInput(value, { label = '', placeholder = '', required = false, onInput }) {
  const input = h('input', 'kit-input ak-res-int');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = 5;
  input.value = value === null || value === undefined ? '' : String(value);
  if (placeholder) input.placeholder = placeholder;
  if (label) input.setAttribute('aria-label', label);
  input.addEventListener('input', () => {
    const raw = input.value.trim();
    const parsed = raw === '' ? (required ? 0 : null) : (/^\d+$/.test(raw) ? Number(raw) : Number.NaN);
    input.classList.toggle('bad', Number.isNaN(parsed));
    onInput(parsed);
  });
  return input;
}

function iconButton(text, label, onClick, disabled = false) {
  const node = button(text, { variant: 'ghost', title: label, onClick, disabled });
  node.classList.add('ak-res-icon');
  node.setAttribute('aria-label', label);
  return node;
}

// ------------------------------------------------------------ müfredat seçimi

/** Program listesi (önbellekli, salt okunur). Kaynağa uyanlar önde. */
async function programsFor(app, resource) {
  const data = await app.api.getCached('/v1/admin/curricula/programs', undefined, { ttlMs: PROGRAMS_TTL });
  const items = data?.items || [];
  const subjects = new Set((resource.subjects || []).map((subject) => subject.id));
  const grades = new Set((resource.gradeLevels || []).map(String));
  const exams = new Set(resource.exams || []);
  const fits = (program) => (!subjects.size || subjects.has(program.subject?.id))
    && ((program.grade !== null && program.grade !== undefined && grades.has(String(program.grade)))
      || (program.exam && exams.has(program.exam)) || (!grades.size && !exams.size));
  // "11. sınıf · Matematik": 13 ayrı "Matematik" programı seviyesiyle ayrışır; sıra sunucunun seviye
  // sırası (5→8, LGS, 9→12, TYT, AYT), alfabetik değil (curriculum-picker.js).
  const label = (program) => programLabel(program);
  const sort = (list) => sortPrograms(list);
  return {
    fitting: sort(items.filter(fits)),
    others: sort(items.filter((program) => !fits(program))),
    label,
    all: items,
  };
}

function programDetail(app, programId) {
  return app.api.getCached(`/v1/admin/curricula/programs/${programId}`, undefined, { ttlMs: PROGRAMS_TTL });
}

/** Optgroup'lu program kutusu (formGrid seçeneği gruplayamıyor). */
function programSelect(groups, value, onChange) {
  const select = h('select', 'kit-select ak-res-program');
  select.setAttribute('aria-label', 'Müfredat programı');
  const none = h('option', '', 'Program seçin');
  none.value = '';
  select.append(none);
  for (const [title, list] of [['Bu kaynağa uygun', groups.fitting], ['Diğer programlar', groups.others]]) {
    if (!list.length) continue;
    const group = h('optgroup');
    group.label = title;
    for (const program of list) {
      const option = h('option', '', groups.label(program));
      option.value = program.id;
      group.append(option);
    }
    select.append(group);
  }
  select.value = value || '';
  select.addEventListener('change', () => onChange(select.value));
  return select;
}

/** Programın konuları → kit seçicisinin satırları (grup = ünite). */
function topicItems(program, topicNames, topicProgram) {
  const items = [];
  for (const unit of [...(program.units || [])].sort(byOrder)) {
    if (unit.retired) continue;
    for (const topic of [...(unit.topics || [])].sort(byOrder)) {
      if (topic.retired) continue;
      topicNames.set(topic.id, topic.title);
      topicProgram.set(topic.id, program.id);
      items.push({ id: topic.id, name: topic.title, group: `${unit.order ?? ''}. ${unit.title}`.replace(/^\. /, ''),
        meta: topic.custom ? 'kuruma özel' : '' });
    }
  }
  return items;
}

/** Kit seçicisinin grup süzgeci "Tüm ünitelar" yazmasın. */
function fixGroupLabel(picker) {
  const option = picker.node.querySelector('.pk-select option[value=""]');
  if (option) option.textContent = 'Tüm üniteler';
}

/**
 * Bölüme konu eşle: program seç → ünite/konu listesinden işaretle. Başka
 * programdan seçilmiş konular korunur (bölüm birden çok programa bağlanabilir).
 * @returns {Promise<string[]|null>}
 */
async function topicDialog(app, { resource, selected, topicNames, topicProgram, memory, title }) {
  let groups;
  try {
    groups = await programsFor(app, resource);
  } catch (failure) {
    app.toast(`Müfredat programları alınamadı: ${problem(failure)}`, 'bad');
    return null;
  }
  const chosen = new Set(selected);
  const known = new Set(groups.all.map((program) => program.id));
  const startProgram = [topicProgram.get(selected[0]), memory.programId, groups.fitting[0]?.id]
    .find((id) => id && known.has(id)) || '';

  const render = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    const slot = h('div', 'ak-res-topic-pick');
    const chosenLine = h('div', 'ak-chips ak-res-chosen-topics');
    let currentIds = new Set();
    const picker = createPicker({ items: [], groupLabel: 'Ünite', placeholder: 'Konu ara', onChange: (ids) => {
      for (const id of currentIds) chosen.delete(id);
      for (const id of ids) chosen.add(id);
      paintChosen();
      set([...chosen]);
    } });
    slot.append(picker.node);
    const paintChosen = () => {
      chosenLine.replaceChildren();
      if (!chosen.size) chosenLine.append(h('span', 'ak-muted', 'Konu seçilmedi (bölüm konusuz kalabilir).'));
      for (const id of chosen) {
        const chip = h('button', 'kit-chip on', `${topicNames.get(id) || id} ✕`);
        chip.type = 'button';
        chip.title = 'Çıkar';
        chip.addEventListener('click', () => {
          chosen.delete(id);
          if (currentIds.has(id)) picker.select([...chosen].filter((item) => currentIds.has(item)));
          paintChosen();
          set([...chosen]);
        });
        chosenLine.append(chip);
      }
    };
    const loadProgram = async (programId) => {
      memory.programId = programId || memory.programId;
      currentIds = new Set();
      picker.setItems([]);
      fixGroupLabel(picker);
      if (!programId) return;
      try {
        const program = await programDetail(app, programId);
        const items = topicItems(program, topicNames, topicProgram);
        currentIds = new Set(items.map((item) => item.id));
        picker.setItems(items);
        fixGroupLabel(picker);
        picker.select([...chosen].filter((id) => currentIds.has(id)));
        paintChosen();
      } catch (failure) {
        app.toast(`Program okunamadı: ${problem(failure)}`, 'bad');
      }
    };
    wrap.append(programSelect(groups, startProgram, loadProgram), slot,
      h('div', 'kit-field-label', 'Seçilen konular'), chosenLine);
    paintChosen();
    set([...chosen]);
    loadProgram(startProgram);
    return wrap;
  };

  const result = await formDialog(app, {
    title: `Konu eşle — ${title}`,
    description: 'Bölümün kapsadığı müfredat konularını işaretleyin. Öğrenci bu bölümü çözünce ilerlemesi bu konulara yazılır.',
    wide: true,
    fields: [{ key: 'topics', label: 'Müfredat', type: 'custom', render }],
    submitLabel: 'Konuları uygula',
    onSubmit: () => [...chosen],
  });
  return Array.isArray(result) ? result : null;
}

// --------------------------------------------------------------- üretim

const GEN_KINDS = { test: 'Test', chapter: 'Konu anlatımı' };

function generateBody(draft, target) {
  const intOr = (raw, fallback) => (raw === null || raw === undefined || raw === '' ? fallback : Number(raw));
  return {
    programId: target.programId || null,
    unitIds: target.unitIds || [],
    topicIds: target.topicIds || [],
    sectionKind: draft.sectionKind === 'chapter' ? 'chapter' : 'test',
    sectionsPerTopic: intOr(draft.sectionsPerTopic, 1),
    questionsPerSection: intOr(draft.questionsPerSection, 12),
    titlePattern: String(draft.titlePattern || '').trim() || 'Test {n} — {topic}',
    groupByUnit: draft.groupByUnit !== false,
    startPage: draft.startPage ? Number(draft.startPage) : null,
    pagesPerSection: draft.pagesPerSection ? Number(draft.pagesPerSection) : null,
    mode: draft.mode === 'replace' ? 'replace' : 'append',
  };
}

/**
 * Konulardan bölüm üret: program (+ isteğe bağlı ünite/konu daraltması) →
 * önizle (`dryRun: true`, kaydetmez) → "Üret ve kaydet" (Akademi kaydeder).
 * @returns {Promise<object|null>} kaydedilen kaynak
 */
async function generateDialog(app, { resource, dirty, memory }) {
  let groups;
  try {
    groups = await programsFor(app, resource);
  } catch (failure) {
    app.toast(`Müfredat programları alınamadı: ${problem(failure)}`, 'bad');
    return null;
  }
  const known = new Set(groups.all.map((program) => program.id));
  const startProgram = [memory.programId, groups.fitting[0]?.id].find((id) => id && known.has(id)) || '';
  const path = `/v1/admin/resources/${resource.id}/toc/generate`;
  // Hedef (program / ünite / konu) kapanışta: formDialog `value`su formGrid'e de
  // gider ve ızgara değişikliğinde özel alanın ilk değerini geri yazardı.
  const target = { programId: startProgram, unitIds: [], topicIds: [] };
  const previewSlot = h('div', 'ak-res-gen-preview');
  previewSlot.append(h('div', 'ak-muted', 'Ayarları seçip “Önizle” ile üretilecek bölümleri görün; önizleme kaydetmez.'));

  const targetField = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    const unitSlot = h('div');
    const topicSlot = h('div', 'ak-res-topic-pick small');
    const state = target;
    let picker = null;
    const publish = () => set({ ...state });
    const loadProgram = async (programId) => {
      state.programId = programId;
      state.unitIds = [];
      state.topicIds = [];
      memory.programId = programId || memory.programId;
      publish();
      unitSlot.replaceChildren();
      topicSlot.replaceChildren();
      if (!programId) return;
      unitSlot.append(h('span', 'ak-muted', 'Program okunuyor…'));
      try {
        const program = await programDetail(app, programId);
        if (state.programId !== programId) return;
        const units = [...(program.units || [])].filter((unit) => !unit.retired).sort(byOrder);
        unitSlot.replaceChildren(h('div', 'kit-field-label', 'Üniteler (seçilmezse hepsi)'),
          multiChips(units.map((unit) => ({ value: unit.id, label: unit.title })), [], (list) => {
            state.unitIds = list;
            publish();
          }).node);
        picker = createPicker({ items: topicItems(program, new Map(), new Map()), groupLabel: 'Ünite',
          placeholder: 'Yalnız belirli konular için arayın', onChange: (ids) => { state.topicIds = ids; publish(); } });
        fixGroupLabel(picker);
        topicSlot.append(h('div', 'kit-field-label', 'Konular (seçilmezse seçili ünitelerin hepsi)'), picker.node);
      } catch (failure) {
        unitSlot.replaceChildren(errorBox(failure));
      }
    };
    wrap.append(programSelect(groups, startProgram, loadProgram), unitSlot, topicSlot);
    loadProgram(startProgram);
    return wrap;
  };

  const previewField = (draft) => {
    const wrap = h('div', 'ak-stack');
    const go = button('Önizle', { onClick: async () => {
      const body = generateBody(draft, target);
      if (!body.programId) { previewSlot.replaceChildren(alertBox('Önce bir müfredat programı seçin.', 'warn')); return; }
      go.disabled = true;
      previewSlot.replaceChildren(skeletonRows(4, 5));
      try {
        const preview = await app.api.post(path, { ...body, dryRun: true });
        const added = (preview.sectionCount || 0) - (body.mode === 'replace' ? 0 : resource.sectionCount || 0);
        previewSlot.replaceChildren(
          alertBox(`Önizleme (kaydedilmedi): ${num((preview.toc || []).length)} ünite · ${num(preview.sectionCount)} bölüm · `
            + `${num(preview.questionCount)} soru${body.mode === 'append' ? ` — ${num(Math.max(0, added))} yeni bölüm eklenecek` : ''}.`, 'info'),
          tocPreview(preview));
      } catch (failure) {
        previewSlot.replaceChildren(errorBox(failure));
      } finally {
        go.disabled = false;
      }
    } });
    wrap.append(go, previewSlot);
    return wrap;
  };

  const notes = [];
  if (dirty) notes.push('Düzenleyicide kaydedilmemiş değişiklik var: üretim KAYITLI içindekilere uygulanır ve bu değişiklikler atılır.');
  const result = await formDialog(app, {
    title: `Konulardan bölüm üret — ${resource.title}`,
    description: ['Seçilen programın konularından bölüm satırları üretilir; sonra düzenleyicide tek tek düzeltebilirsiniz.',
      ...notes].join('\n'),
    wide: true,
    value: { sectionKind: 'test', sectionsPerTopic: 1, questionsPerSection: 12, titlePattern: 'Test {n} — {topic}',
      groupByUnit: true, mode: 'append' },
    fields: [
      { key: 'target', label: 'Müfredat programı', type: 'custom', render: targetField,
        validate: () => (target.programId ? null : 'Program seçin.') },
      { key: 'sectionKind', label: 'Bölüm türü', type: 'select', options: optionsOf(GEN_KINDS) },
      { key: 'mode', label: 'Mevcut içindekiler', type: 'select', options: [
        { value: 'append', label: 'Sonuna ekle' }, { value: 'replace', label: 'Yerine koy (mevcutlar silinir)' }],
      hint: 'Yerine koymada ilerlemesi olan bölüm varsa Akademi işlemi durdurur.' },
      { key: 'sectionsPerTopic', label: 'Konu başına bölüm', type: 'number', required: true, min: 1, max: 20 },
      { key: 'questionsPerSection', label: 'Bölüm başına soru', type: 'number', required: true, min: 0, max: 200 },
      { key: 'titlePattern', label: 'Bölüm adı kalıbı', maxLength: 120, wide: true,
        hint: 'Yer tutucular: {n} sıra numarası, {topic} konu adı, {unit} ünite adı.' },
      { key: 'startPage', label: 'Başlangıç sayfası (isteğe bağlı)', type: 'number', min: 1 },
      { key: 'pagesPerSection', label: 'Bölüm başına sayfa (isteğe bağlı)', type: 'number', min: 1 },
      { key: 'groupByUnit', label: 'Müfredat ünitelerine göre grupla', type: 'checkbox' },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: previewField },
    ],
    submitLabel: 'Üret ve kaydet',
    onSubmit: (draft, { key }) => app.api.post(path, { ...generateBody(draft, target), dryRun: false }, { key }),
  });
  return result && result !== true ? result : null;
}
