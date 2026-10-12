// BBD Akademi — Denemeler › Cevap anahtarı ve soru → konu eşlemesi (F-E3).
//
// Cevap anahtarı kitapçık başına, bölüm başına bir dizidir (A–E; `*` iptal
// edilen soru). Anahtar kaydedilince cevap dizisiyle girilmiş sonuçlar
// Akademi'de yeniden hesaplanır. Soru → konu eşlemesi konu bazlı analizin
// (en çok yanlış yapılan konular, öğrencinin tekrar listesi) kaynağıdır;
// konular bölümün müfredat programından gelir (salt okunur, ETag önbelleği).
// Eşleme elle, aralık atamasıyla ya da CSV/XLSX dosyasından girilir.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow, hintBox } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  actionButton, empty, errorBox, fileDrop, loader, num, selectBox, toolbar, uploadProgress,
} from './common.js';
import { sectionLabel, sortedSections, uploadFile } from './exams-shared.js';

export function keysTab(app, exam, { onChanged } = {}) {
  const node = h('div', 'ak-stack');
  const parts = [];
  if (app.can('admin_getAnswerKeys')) parts.push(answerKeysCard(app, exam, { onChanged }));
  if (app.can('admin_getQuestionTopics')) parts.push(topicsCard(app, exam, { onChanged }));
  if (!parts.length) node.append(alertBox('Cevap anahtarı ve konu eşlemesini görme yetkiniz yok.', 'warn'));
  for (const part of parts) node.append(part.node);
  return {
    node,
    reload: () => parts.forEach((part) => part.reload()),
    destroy: () => parts.forEach((part) => part.destroy?.()),
  };
}

const cleanAnswers = (text) => String(text || '').toUpperCase().replace(/[^A-E*]/g, '');

// ----------------------------------------------------------- cevap anahtarı

function answerKeysCard(app, exam, { onChanged }) {
  const sections = sortedSections(exam);
  const content = h('div', 'ak-stack ak-card-pad');
  const box = card('Cevap anahtarı', content, 'kitapçık başına');
  let state = new Map();                    // kitapçık → Map(sectionId → {answers, questionOrder})
  let booklet = null;
  let dirty = false;

  const view = loader(() => app.api.get(`/v1/admin/exams/${exam.id}/answer-keys`), (data) => {
    state = new Map();
    const letters = new Set(exam.bookletTypes?.length ? exam.bookletTypes : ['A']);
    for (const item of data?.booklets || []) letters.add(item.bookletType);
    for (const letter of [...letters].sort()) state.set(letter, new Map());
    for (const item of data?.booklets || []) {
      for (const value of item.sections || []) {
        state.get(item.bookletType).set(value.sectionId, { answers: value.answers || '', questionOrder: value.questionOrder ?? null });
      }
    }
    booklet = booklet && state.has(booklet) ? booklet : [...state.keys()][0];
    dirty = false;
    return paint();
  });

  function paint() {
    const wrap = h('div', 'ak-stack');
    const letters = [...state.keys()];
    if (letters.length > 1) {
      const chips = chipRow(letters.map((letter) => ({ key: letter, label: `${letter} kitapçığı` })), booklet, (key) => {
        booklet = key || booklet;
        chips.set(booklet);
        rows.replaceChildren(...sections.map((item) => sectionRow(item)));
      });
      chips.node.classList.add('ak-ex-chips');
      wrap.append(chips.node);
    }
    wrap.append(hintBox('Her bölüm için cevapları sırayla yazın ya da yapıştırın (A–E). İptal edilen soru için * '
      + 'yazın. Boşluk, virgül ve satır sonları kendiliğinden ayıklanır.'));
    const rows = h('div', 'ak-ex-key-list');
    rows.append(...sections.map((item) => sectionRow(item)));
    const error = h('div', 'kit-field-error');
    const save = actionButton(app, 'admin_putAnswerKeys', 'Cevap anahtarını kaydet', { variant: 'primary',
      onClick: () => submit(save, error) });
    wrap.append(rows, error, toolbar(h('span', 'ak-muted', 'Kaydedince cevap dizisiyle girilmiş sonuçlar yeniden hesaplanır.'),
      h('span', 'kit-spacer'), save));
    return wrap;
  }

  function sectionRow(item) {
    const entry = state.get(booklet).get(item.id) || { answers: '', questionOrder: null };
    const row = h('div', 'ak-ex-key-row');
    const label = h('span', 'ak-cell-stack');
    label.append(h('span', '', sectionLabel(item)), h('small', '', `${item.questionCount} soru`));
    const input = h('input', 'kit-input ak-ex-answers');
    input.value = entry.answers;
    input.spellcheck = false;
    input.setAttribute('aria-label', `${booklet} kitapçığı ${sectionLabel(item)} cevapları`);
    const counter = h('small', 'ak-ex-counter');
    const paintCounter = () => {
      const length = input.value.length;
      counter.textContent = `${length}/${item.questionCount}`;
      counter.classList.toggle('bad', length > 0 && length !== item.questionCount);
      counter.classList.toggle('good', length === item.questionCount);
    };
    input.addEventListener('input', () => {
      const clean = cleanAnswers(input.value);
      if (clean !== input.value) input.value = clean;
      state.get(booklet).set(item.id, { ...entry, answers: clean });
      dirty = true;
      paintCounter();
    });
    paintCounter();
    row.append(label, input, counter);
    return row;
  }

  async function submit(save, error) {
    error.textContent = '';
    const booklets = [];
    const problems = [];
    for (const [letter, map] of state) {
      const values = [];
      for (const item of sections) {
        const answers = map.get(item.id)?.answers || '';
        if (!answers) continue;
        if (answers.length !== item.questionCount) {
          problems.push(`${letter} kitapçığı ${sectionLabel(item)}: ${answers.length}/${item.questionCount} cevap`);
        }
        values.push({ sectionId: item.id, answers, questionOrder: map.get(item.id)?.questionOrder ?? null });
      }
      if (values.length) booklets.push({ bookletType: letter, sections: values });
    }
    if (problems.length) {
      error.textContent = `Cevap sayısı soru sayısıyla aynı olmalı: ${problems.join(' · ')}.`;
      return;
    }
    if (!booklets.length) { error.textContent = 'Kaydedilecek cevap yok.'; return; }
    save.disabled = true;
    try {
      await app.api.put(`/v1/admin/exams/${exam.id}/answer-keys`, { booklets });
      dirty = false;
      app.toast('Cevap anahtarı kaydedildi.', 'good');
      onChanged?.();
    } catch (failure) {
      error.textContent = failure?.message || 'Kaydedilemedi.';
    } finally {
      save.disabled = false;
    }
  }

  content.append(view.node);
  return { node: box, reload: () => { if (!dirty) view.reload(); }, destroy() {} };
}

// ------------------------------------------------------------ soru → konu

function topicsCard(app, exam, { onChanged }) {
  const sections = sortedSections(exam);
  const content = h('div', 'ak-stack ak-card-pad');
  const box = card('Soru → konu eşlemesi', content, 'konu bazlı analiz için');
  const assigned = new Map();               // `${sectionId}:${no}` → Set(topicId)
  const programs = new Map();               // sectionId → {programId, units}
  const topicTitle = new Map();
  let active = sections[0]?.id || null;
  let dirty = false;

  const keyOf = (sectionId, no) => `${sectionId}:${no}`;

  async function loadProgram(section, programId) {
    if (!programId) { programs.set(section.id, { programId: '', units: [] }); return; }
    const program = await app.api.getCached(`/v1/admin/curricula/programs/${programId}`, undefined, { ttlMs: 10 * 60_000 });
    const units = (program?.units || []).filter((unit) => !unit.retired).map((unit) => ({
      id: unit.id, title: unit.title,
      topics: (unit.topics || []).filter((topic) => !topic.retired),
    }));
    for (const unit of units) for (const topic of unit.topics) topicTitle.set(topic.id, topic.title);
    programs.set(section.id, { programId, units });
  }

  const view = loader(async () => {
    const [data] = await Promise.all([
      app.api.get(`/v1/admin/exams/${exam.id}/question-topics`),
      ...sections.map((item) => loadProgram(item, item.programId || '').catch(() => programs.set(item.id,
        { programId: item.programId || '', units: [], failed: true }))),
    ]);
    assigned.clear();
    for (const item of data?.items || []) {
      if ((item.topicIds || []).length) assigned.set(keyOf(item.sectionId, item.questionNo), new Set(item.topicIds));
    }
    dirty = false;
    return data;
  }, () => paint());

  function coverage(section) {
    let count = 0;
    for (let no = 1; no <= section.questionCount; no += 1) if (assigned.get(keyOf(section.id, no))?.size) count += 1;
    return count;
  }

  function paint() {
    const wrap = h('div', 'ak-stack');
    if (!sections.length) return empty('Bölüm yok', 'Önce denemeye bölüm ekleyin.');
    const chips = chipRow(sections.map((item) => ({ key: item.id, label: sectionLabel(item),
      count: `${coverage(item)}/${item.questionCount}` })), active, (key) => {
      active = key || active;
      chips.set(active);
      panel.replaceChildren(sectionPanel(sections.find((item) => item.id === active)));
    });
    chips.node.classList.add('ak-ex-chips');
    const panel = h('div', 'ak-stack');
    panel.append(sectionPanel(sections.find((item) => item.id === active)));
    const error = h('div', 'kit-field-error');
    const save = actionButton(app, 'admin_putQuestionTopics', 'Eşlemeyi kaydet', { variant: 'primary',
      onClick: () => submit(save, error) });
    wrap.append(chips.node, panel, error, toolbar(h('span', 'ak-muted',
      'Bir soruya birden çok konu bağlanabilir.'), h('span', 'kit-spacer'), save));
    wrap.append(importBox());
    // Sayaçlar değişikliklerde güncellenir.
    wrap.refreshCounts = () => chips.counts(Object.fromEntries(sections.map((item) =>
      [item.id, `${coverage(item)}/${item.questionCount}`])));
    refreshCounts = wrap.refreshCounts;
    return wrap;
  }
  let refreshCounts = () => {};

  /** <select> konu listesi (ünite grupları). */
  function topicSelect(section, placeholder = 'Konu ekle…') {
    const select = h('select', 'kit-select ak-ex-topic-select');
    const first = h('option', '', placeholder);
    first.value = '';
    select.append(first);
    for (const unit of programs.get(section.id)?.units || []) {
      const group = h('optgroup');
      group.label = unit.title;
      for (const topic of unit.topics) {
        const option = h('option', '', topic.title);
        option.value = topic.id;
        group.append(option);
      }
      select.append(group);
    }
    return select;
  }

  function sectionPanel(section) {
    const wrap = h('div', 'ak-stack');
    const info = programs.get(section.id) || { units: [] };
    if (info.failed) wrap.append(errorBox(new Error('Bölümün müfredat programı okunamadı.'), () => view.reload()));
    if (!info.programId || !info.units.length) {
      wrap.append(alertBox(info.programId ? 'Bu programda konu yok.' : 'Bu bölüme müfredat programı bağlı değil. '
        + 'Konu listesi için aşağıdan program seçin (kalıcı bağlamak için denemeyi düzenleyin).', 'info'));
      const picker = selectBox([{ value: '', label: 'Program yükleniyor…' }], info.programId || '', async (value) => {
        try {
          await loadProgram(section, value);
          wrap.replaceWith(sectionPanel(section));
        } catch (failure) { app.toast(failure?.message || 'Program okunamadı.', 'bad'); }
      }, { label: 'Müfredat programı' });
      app.api.getCached('/v1/admin/curricula/programs', { subject: section.subject?.id }, { ttlMs: 10 * 60_000 })
        .then((data) => picker.options([{ value: '', label: 'Program seçin' },
          ...(data?.items || []).map((item) => ({ value: item.id, label: `${item.title}${item.exam ? ` · ${item.exam}` : ''}` }))]))
        .catch(() => picker.options([{ value: '', label: 'Program listesi alınamadı' }]));
      wrap.append(toolbar(picker.node));
      if (!info.units.length) return wrap;
    }

    // Aralık ataması: "1–5 → Üslü sayılar".
    const from = h('input', 'kit-input ak-ex-count');
    from.inputMode = 'numeric';
    from.value = '1';
    from.setAttribute('aria-label', 'İlk soru');
    const to = h('input', 'kit-input ak-ex-count');
    to.inputMode = 'numeric';
    to.value = String(section.questionCount);
    to.setAttribute('aria-label', 'Son soru');
    const rangeTopic = topicSelect(section, 'Konu seçin');
    const list = h('div', 'ak-ex-topic-list');
    wrap.append(toolbar(h('span', 'ak-muted', 'Aralığa ata: soru'), from, h('span', '', '–'), to, rangeTopic,
      button('Ata', { onClick: () => {
        const start = Math.max(1, Number(from.value) || 1);
        const end = Math.min(section.questionCount, Number(to.value) || section.questionCount);
        if (!rangeTopic.value || start > end) { app.toast('Geçerli bir aralık ve konu seçin.', 'warn'); return; }
        for (let no = start; no <= end; no += 1) {
          const key = keyOf(section.id, no);
          if (!assigned.has(key)) assigned.set(key, new Set());
          assigned.get(key).add(rangeTopic.value);
        }
        changed();
        paintList();
      } }),
      h('span', 'kit-spacer'),
      button('Bölümü temizle', { variant: 'ghost', onClick: () => {
        for (let no = 1; no <= section.questionCount; no += 1) assigned.delete(keyOf(section.id, no));
        changed();
        paintList();
      } })));
    wrap.append(list);

    function paintList() {
      list.replaceChildren();
      for (let no = 1; no <= section.questionCount; no += 1) list.append(questionRow(section, no, paintList));
    }
    paintList();
    return wrap;
  }

  function questionRow(section, no, repaint) {
    const key = keyOf(section.id, no);
    const row = h('div', 'ak-ex-topic-row');
    row.append(h('span', 'ak-ex-qno', `${no}.`));
    const chosen = h('div', 'ak-chips');
    for (const topicId of assigned.get(key) || []) {
      const chip = h('button', 'kit-chip on', `${topicTitle.get(topicId) || topicId} ✕`);
      chip.type = 'button';
      chip.title = 'Konuyu kaldır';
      chip.addEventListener('click', () => {
        assigned.get(key)?.delete(topicId);
        if (!assigned.get(key)?.size) assigned.delete(key);
        changed();
        repaint();
      });
      chosen.append(chip);
    }
    if (!assigned.get(key)?.size) chosen.append(h('span', 'ak-muted', 'konu yok'));
    const select = topicSelect(section);
    select.addEventListener('change', () => {
      if (!select.value) return;
      if (!assigned.has(key)) assigned.set(key, new Set());
      assigned.get(key).add(select.value);
      changed();
      repaint();
    });
    row.append(chosen, select);
    return row;
  }

  function changed() {
    dirty = true;
    refreshCounts();
  }

  async function submit(save, error) {
    error.textContent = '';
    const items = [];
    for (const section of sections) {
      for (let no = 1; no <= section.questionCount; no += 1) {
        const topics = assigned.get(keyOf(section.id, no));
        if (topics?.size) items.push({ sectionId: section.id, questionNo: no, topicIds: [...topics] });
      }
    }
    save.disabled = true;
    try {
      await app.api.put(`/v1/admin/exams/${exam.id}/question-topics`, { items });
      dirty = false;
      app.toast(`Soru → konu eşlemesi kaydedildi (${num(items.length)} soru).`, 'good');
      onChanged?.();
    } catch (failure) {
      error.textContent = failure?.message || 'Kaydedilemedi.';
    } finally {
      save.disabled = false;
    }
  }

  /** CSV/XLSX'ten toplu eşleme (sütunlar: bölüm, soru no, konu kimliği/leri). */
  function importBox() {
    const wrap = h('div', 'ak-stack ak-ex-topic-import');
    const result = h('div', 'ak-stack');
    const progress = uploadProgress();
    let file = null;
    const replace = h('input', 'kit-check');
    replace.type = 'checkbox';
    replace.checked = true;
    const replaceLabel = h('label', 'ak-inline');
    replaceLabel.append(replace, h('span', '', 'Mevcut eşlemenin yerine geçsin'));
    const go = actionButton(app, 'admin_importQuestionTopics', 'Dosyadan yükle', { onClick: async () => {
      if (!file) { app.toast('Önce dosya seçin.', 'warn'); return; }
      go.disabled = true;
      result.replaceChildren(progress.node);
      try {
        const response = await uploadFile(app, `/v1/admin/exams/${exam.id}/question-topics/import`, file,
          { fields: { replace: replace.checked }, progress });
        const data = response.data || {};
        const errors = data.errors || [];
        const nodes = [alertBox(`${num(data.applied || 0)} soru eşlendi${errors.length ? `, ${num(errors.length)} satır hatalı` : ''}.`,
          errors.length ? 'warn' : 'good')];
        if (errors.length) {
          nodes.push(dataTable({
            columns: [
              { key: 'rowNo', label: 'Satır', width: '64px', align: 'num' },
              { key: 'column', label: 'Sütun', width: '110px', cell: (row) => row.column || '—' },
              { key: 'message', label: 'Hata', width: 'minmax(0, 2fr)' },
            ],
            rows: errors,
            dense: true,
          }).node);
        }
        result.replaceChildren(...nodes);
        onChanged?.();
        view.reload();
      } catch (failure) {
        result.replaceChildren(errorBox(failure));
      } finally {
        go.disabled = false;
      }
    } });
    const drop = fileDrop({ accept: ['csv', 'xlsx', 'xls'], maxMb: 10, label: 'Eşleme dosyasını seçin ya da bırakın',
      onFile: (picked) => { file = picked; } });
    wrap.append(h('b', '', 'Dosyadan yükle'),
      h('div', 'ak-muted', 'Sütunlar: bölüm, soru no, konu kimliği (birden çok konu için ; ile ayırın).'),
      drop.node, toolbar(replaceLabel, h('span', 'kit-spacer'), go), result);
    return wrap;
  }

  content.append(view.node);
  return { node: box, reload: () => { if (!dirty) view.reload(); }, destroy() {} };
}
