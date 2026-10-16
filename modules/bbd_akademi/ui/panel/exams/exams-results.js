// BBD Akademi — Denemeler › Sonuçlar ve Dışa aktar sekmeleri (F-E5, F-E6, F-E8).
//
// Sonuç tablosu imleçlidir (`admin_listExamResults`); ders başına doğru /
// yanlış / boş ve net, toplam net, puan ve sıralar liste yükünden gelir —
// satır başına istek atılmaz. Tekil giriş iki biçimdedir: D/Y/B sayıları ya da
// cevap dizisi (anahtar girilmişse D/Y/B Akademi'de hesaplanır). Net ve sıra
// her zaman Akademi'nin yanıtındandır; formdaki net yalnız tahmindir.

import { h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../../ui-kit/layout.js';
import { newKey } from '../api.js';
import {
  actionButton, confirmAction, exportJob, formDialog, num, pagedList, runAction, selectBox,
  studentPicker, toolbar,
} from '../common.js';
import {
  RESULT_SOURCE, estimateNet, netText, sectionLabel, sortedSections, studentCell,
} from './exams-shared.js';
import { documentButtons } from '../documents.js';
import { examResultDoc, examResultsDoc, mapLimit, mergeDocs } from '../documents-logic.js';

/** Denemeye katılan sınıflar (tanımda seçilmediyse bütün sınıflar). */
async function examClassOptions(app, exam, emptyLabel = 'Tüm sınıflar') {
  const groups = await app.lookups.classGroups();
  const allowed = new Set(exam.classGroupIds || []);
  const list = allowed.size ? groups.filter((item) => allowed.has(item.id)) : groups;
  return [{ value: '', label: emptyLabel }, ...list.map((item) => ({ value: item.id, label: item.name }))];
}

// ---------------------------------------------------------------- sonuçlar

export function resultsTab(app, exam, { onChanged } = {}) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', missingOnly: false };
  const sections = sortedSections(exam);

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; list.reload();
  }, { label: 'Sınıf' });
  let classNames = new Map();
  examClassOptions(app, exam).then((options) => {
    classSelect.options(options);
    classNames = new Map(options.map((item) => [item.value, item.label]));
  }).catch(() => {});
  const missing = h('label', 'ak-inline ak-ex-toggle');
  const missingCheck = h('input', 'kit-check');
  missingCheck.type = 'checkbox';
  missingCheck.addEventListener('change', () => { filters.missingOnly = missingCheck.checked; list.reload(); });
  missing.append(missingCheck, h('span', '', 'Yalnız sonucu eksik öğrenciler'));
  const add = actionButton(app, 'admin_putExamResult', 'Sonuç gir', { variant: 'primary',
    onClick: () => edit(null, null) });
  // Belgeler: sınıf sonuç listesi (süzgeçteki bütün sonuçlar, sayfalı okunur) ve
  // seçili öğrencilerin sonuç belgeleri (tek iş; Akademi'ye aynı anda en çok 4 istek).
  const scopeName = () => (filters.classGroupId ? classNames.get(filters.classGroupId) || '' : '');
  const listDoc = documentButtons(app, { title: 'Sonuç listesi', label: 'Sonuç listesi',
    build: async (progress) => {
      progress(0, 0, 'Sonuçlar okunuyor…');
      const rows = await app.api.all(`/v1/admin/exams/${exam.id}/results`,
        { classGroupId: filters.classGroupId || undefined }, { limit: 100, max: 3000 });
      return examResultsDoc(exam, rows.filter((row) => row.updatedAt || row.absent), { classGroupName: scopeName() });
    } });
  const studentDocs = documentButtons(app, { title: 'Sonuç belgeleri', label: 'Seçilenlerin belgesi',
    build: async (progress, state) => {
      const chosen = list.table.selectedRows().filter((row) => row.updatedAt || row.absent);
      if (!chosen.length) throw new Error('Önce listeden sonucu olan öğrencileri seçin.');
      const results = await mapLimit(chosen, 4, (row) => (state.cancelled ? null
        : app.api.get(`/v1/students/${row.student.id}/exams/${exam.id}`)
          .then((result) => examResultDoc(result, row.student))),
      (done, total) => progress(done, total, `Sonuç belgeleri okunuyor… ${done} / ${total}`));
      return mergeDocs(results, { title: `${exam.name} sonuç belgeleri` });
    } });
  node.append(toolbar(classSelect.node, missing, h('span', 'kit-spacer'), listDoc, studentDocs, add));

  const columns = [
    { key: 'student', label: 'Öğrenci', width: 'minmax(170px, 1.6fr)', cell: (row) => studentCell(row.student) },
    { key: 'booklet', label: 'Kit.', width: '44px', cell: (row) => row.bookletType || '—' },
    ...sections.map((item) => ({
      key: `s-${item.id}`,
      label: sectionLabel(item),
      title: `${item.title} · ${item.questionCount} soru`,
      align: 'num',
      width: '84px',
      cell: (row) => sectionCell(row, item),
    })),
    { key: 'total', label: 'Toplam net', align: 'num', width: '92px', cell: (row) => {
      if (row.absent) return badge('Girmedi', 'dim');
      return row.totalNet === null || row.totalNet === undefined ? '—' : h('b', '', netText(row.totalNet));
    } },
    { key: 'score', label: 'Puan', align: 'num', width: '84px',
      cell: (row) => (row.estimatedScore === null || row.estimatedScore === undefined ? '—' : num(row.estimatedScore, 2)) },
    { key: 'rank', label: 'Sıra', width: '120px', cell: (row) => rankCell(row.ranks) },
    { key: 'source', label: 'Kaynak', width: '96px', cell: (row) => (row.updatedAt
      ? h('small', 'ak-muted', RESULT_SOURCE[row.source] || row.source || '—') : '—') },
    { key: 'actions', label: '', width: '140px', cell: (row) => {
      const actions = h('div', 'ak-row-actions');
      const hasResult = Boolean(row.updatedAt);
      actions.append(actionButton(app, 'admin_putExamResult', hasResult ? 'Düzenle' : 'Sonuç gir', {
        variant: 'ghost', onClick: () => edit(row.student, hasResult ? row : null) }));
      if (hasResult) {
        actions.append(actionButton(app, 'admin_deleteExamResult', 'Sil', { variant: 'ghost',
          onClick: () => remove(row) }));
      }
      return actions;
    } },
  ];

  const list = pagedList({
    app,
    path: `/v1/admin/exams/${exam.id}/results`,
    query: () => ({ classGroupId: filters.classGroupId || undefined, missingOnly: filters.missingOnly || undefined }),
    columns,
    rowKey: (row) => row.student?.id,
    dense: true,
    selectable: true,
    empty: { title: 'Bu süzgece uyan sonuç yok',
      text: 'Sonuçları “Toplu yükleme” sekmesinden dosyayla yükleyin ya da “Sonuç gir” ile tek tek girin.' },
  });
  node.append(list.node);

  async function edit(student, existing) {
    const saved = await resultDialog(app, exam, { student, existing });
    if (!saved) return;
    app.toast(`${saved.student?.displayName || 'Öğrenci'}: sonuç kaydedildi (net ${netText(saved.totalNet)}).`, 'good');
    const id = saved.student?.id;
    if (list.rows().some((row) => row.student?.id === id)) {
      if (filters.missingOnly) list.remove((row) => row.student?.id === id);
      else list.replace((row) => row.student?.id === id, saved);
    } else {
      list.reload();
    }
    onChanged?.();
  }

  async function remove(row) {
    const ok = await confirmAction(app, {
      title: `${row.student?.displayName || 'Öğrenci'}: sonucu sil`,
      description: 'Bu denemedeki sonucu silinir; sıralamalar ve analiz yeniden hesaplanır.',
      confirmLabel: 'Sonucu sil',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/exams/${exam.id}/results/${row.student.id}`),
      { success: 'Sonuç silindi.' });
    if (done !== undefined) {
      list.remove((item) => item.student?.id === row.student.id);
      onChanged?.();
    }
  }

  return { node, reload: () => list.reload(), destroy() { list.destroy(); } };
}

function sectionCell(row, section) {
  if (row.absent || !row.updatedAt) return '—';
  const value = (row.sections || []).find((item) => item.sectionId === section.id);
  if (!value) return '—';
  const cell = h('div', 'ak-cell-stack ak-ex-net');
  cell.append(h('span', '', netText(value.net)), h('small', '', `${value.correct}/${value.wrong}/${value.blank}`));
  cell.title = `${section.title}: ${value.correct} doğru · ${value.wrong} yanlış · ${value.blank} boş · net ${netText(value.net)}`;
  return cell;
}

function rankCell(ranks) {
  if (!ranks || (ranks.institution == null && ranks.classGroup == null && ranks.publisherNational == null)) return '—';
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', ranks.institution != null ? `Kurum ${num(ranks.institution)}` : '—'),
    h('small', '', [ranks.classGroup != null ? `sınıf ${num(ranks.classGroup)}` : '',
      ranks.publisherNational != null ? `TR ${num(ranks.publisherNational)}` : ''].filter(Boolean).join(' · ')));
  return cell;
}

/**
 * Tekil sonuç: öğrenci (yeni girişte seçilir), girmedi, kitapçık, D/Y/B ya da
 * cevap dizisi, yayının Türkiye sırası. Kayıt PUT'tur (aynı öğrenciye
 * ikinci kez girilirse üzerine yazar).
 */
async function resultDialog(app, exam, { student = null, existing = null } = {}) {
  const sections = sortedSections(exam);
  const booklets = exam.bookletTypes || [];
  const answersAvailable = exam.answerKeyStatus && exam.answerKeyStatus !== 'none';
  const counts = {};
  for (const item of sections) {
    const found = (existing?.sections || []).find((value) => value.sectionId === item.id);
    counts[item.id] = { correct: found?.correct ?? '', wrong: found?.wrong ?? '', blank: found?.blank ?? '',
      answers: '' };
  }

  const fields = [
    student ? null : { key: 'student', label: 'Öğrenci', type: 'custom', required: true,
      render: (draft, set) => {
        const wrap = h('div', 'ak-stack');
        const chosen = h('div', 'ak-muted', 'Henüz seçilmedi.');
        const picker = studentPicker(app, { onPick: (picked) => {
          set(picked);
          chosen.replaceChildren(studentCell(picked));
        } });
        wrap.append(picker.node, chosen);
        return wrap;
      } },
    { key: 'absent', label: 'Sınava girmedi', type: 'checkbox' },
    // Tek kitapçıkta seçim yok: değer kendiliğinden gönderilir.
    booklets.length > 1 ? { key: 'bookletType', label: 'Kitapçık', type: 'select',
      options: booklets.map((letter) => ({ value: letter, label: `${letter} kitapçığı` })) } : null,
    answersAvailable ? { key: 'mode', label: 'Giriş biçimi', type: 'select', options: [
      { value: 'counts', label: 'Doğru / yanlış / boş sayıları' },
      { value: 'answers', label: 'Cevap dizisi (anahtardan hesaplanır)' }] } : null,
    { key: 'publisherNationalRank', label: 'Yayının Türkiye sırası', type: 'number', min: 1,
      hint: 'Yayın verdiyse; boş bırakılabilir.' },
    { key: 'sections', label: 'Bölümler', type: 'custom',
      render: (draft, set) => sectionsInput(exam, sections, counts, draft, set),
      validate: (raw, draft) => (!draft.absent && raw === null
        ? 'Bir bölümde doğru + yanlış + boş soru sayısını aşıyor.' : null) },
  ];

  return formDialog(app, {
    title: `Sonuç — ${student?.displayName || 'öğrenci seçin'}`,
    description: `${exam.name}. Net deneme kuralıyla Akademi'de hesaplanır; aşağıdaki net yalnız tahmindir.`,
    wide: true,
    value: {
      absent: Boolean(existing?.absent),
      bookletType: existing?.bookletType || booklets[0] || null,
      mode: 'counts',
      publisherNationalRank: existing?.ranks?.publisherNational ?? null,
    },
    fields,
    submitLabel: 'Sonucu kaydet',
    onSubmit: (draft) => {
      const target = student || draft.student;
      const mode = answersAvailable ? (draft.mode || 'counts') : 'counts';
      const body = {
        absent: Boolean(draft.absent),
        bookletType: draft.bookletType || booklets[0] || null,
        publisherNationalRank: draft.publisherNationalRank ? Number(draft.publisherNationalRank) : null,
      };
      if (!draft.absent) {
        if (mode === 'answers') {
          body.answers = sections.map((item) => ({ sectionId: item.id,
            answers: String(counts[item.id].answers || '').toUpperCase() }));
        } else {
          body.sections = sections.map((item) => {
            const value = counts[item.id];
            const correct = Number(value.correct) || 0;
            const wrong = Number(value.wrong) || 0;
            const blank = value.blank === '' || value.blank === null
              ? Math.max(0, item.questionCount - correct - wrong) : Number(value.blank) || 0;
            return { sectionId: item.id, correct, wrong, blank };
          });
        }
      }
      return app.api.put(`/v1/admin/exams/${exam.id}/results/${target.id}`, body);
    },
  });
}

/** Bölüm satırları: D/Y/B (boş kendiliğinden hesaplanır) ya da cevap dizisi. */
function sectionsInput(exam, sections, counts, draft, set) {
  const node = h('div', 'ak-ex-result-grid');
  const errors = h('div', 'kit-field-error');
  const totalLine = h('div', 'ak-muted');

  function paint() {
    node.replaceChildren();
    if (draft.absent) {
      node.append(alertBox('Öğrenci sınava girmedi olarak kaydedilir; ders sonuçları gönderilmez.', 'info'));
      set(counts);
      return;
    }
    const answersMode = draft.mode === 'answers';
    const head = h('div', `ak-ex-result-row ak-ex-row-head${answersMode ? ' ak-ex-mode-answers' : ''}`);
    head.append(h('span', '', 'Bölüm'));
    if (answersMode) head.append(h('span', '', 'Cevaplar (A–E, boş için - ya da boşluk)'));
    else head.append(h('span', '', 'Doğru'), h('span', '', 'Yanlış'), h('span', '', 'Boş'), h('span', '', 'Net (tahmin)'));
    node.append(head);
    for (const item of sections) node.append(answersMode ? answersRow(item) : countsRow(item));
    node.append(totalLine, errors);
    recalc();
  }

  function countsRow(item) {
    const value = counts[item.id];
    const row = h('div', 'ak-ex-result-row');
    const label = h('span', 'ak-cell-stack');
    label.append(h('span', '', sectionLabel(item)), h('small', '', `${item.questionCount} soru`));
    const netNode = h('span', 'ak-num ak-ex-net-preview', '—');
    // Boş, elle yazılmadıkça soru sayısından hesaplanır (D/Y değişince güncellenir).
    let blankManual = false;
    const inputs = ['correct', 'wrong', 'blank'].map((field) => {
      const input = h('input', 'kit-input ak-ex-count');
      input.inputMode = 'numeric';
      input.value = value[field] === '' || value[field] === null ? '' : String(value[field]);
      input.setAttribute('aria-label', `${sectionLabel(item)} ${field === 'correct' ? 'doğru' : field === 'wrong' ? 'yanlış' : 'boş'}`);
      input.addEventListener('input', () => {
        value[field] = input.value.trim() === '' ? '' : Number(input.value.replace(/\D/g, ''));
        if (field === 'blank') blankManual = input.value.trim() !== '';
        else if (!blankManual) { value.blank = ''; inputs[2].value = ''; }
        recalc();
      });
      return input;
    });
    const blankInput = inputs[2];
    row.append(label, ...inputs, netNode);
    row.recalc = () => {
      const correct = Number(value.correct) || 0;
      const wrong = Number(value.wrong) || 0;
      blankInput.placeholder = String(Math.max(0, item.questionCount - correct - wrong));
      const blank = value.blank === '' ? Math.max(0, item.questionCount - correct - wrong) : Number(value.blank) || 0;
      const over = correct + wrong + blank > item.questionCount;
      row.classList.toggle('bad', over);
      netNode.textContent = netText(estimateNet(exam, correct, wrong));
      return { over, net: estimateNet(exam, correct, wrong) };
    };
    rowsState.push(row);
    return row;
  }

  function answersRow(item) {
    const value = counts[item.id];
    const row = h('div', 'ak-ex-result-row ak-ex-mode-answers');
    const label = h('span', 'ak-cell-stack');
    label.append(h('span', '', sectionLabel(item)), h('small', '', `${item.questionCount} soru`));
    const input = h('input', 'kit-input ak-ex-answers');
    input.value = value.answers || '';
    input.maxLength = item.questionCount;
    input.spellcheck = false;
    input.setAttribute('aria-label', `${sectionLabel(item)} cevap dizisi`);
    const counter = h('small', 'ak-muted', '');
    input.addEventListener('input', () => {
      input.value = input.value.toUpperCase().replace(/[^A-E -]/g, '');
      value.answers = input.value;
      recalc();
    });
    const cell = h('div', 'ak-cell-stack');
    cell.append(input, counter);
    row.append(label, cell);
    row.recalc = () => {
      const length = (value.answers || '').length;
      counter.textContent = `${length}/${item.questionCount}`;
      return { over: length > item.questionCount, net: null };
    };
    rowsState.push(row);
    return row;
  }

  let rowsState = [];
  function recalc() {
    let total = 0;
    let over = false;
    for (const row of rowsState) {
      const state = row.recalc();
      over = over || state.over;
      if (state.net !== null) total += state.net;
    }
    totalLine.textContent = draft.mode === 'answers' ? 'Net, cevap anahtarıyla Akademi\'de hesaplanır.'
      : `Toplam net (tahmin): ${netText(Math.round(total * 100) / 100)}`;
    errors.textContent = over ? 'Bir bölümde doğru + yanlış + boş soru sayısını aşıyor.' : '';
    set(over ? null : counts);
  }

  let lastKey = '';
  const control = {
    node: h('div', 'ak-stack'),
    update(next) {
      const key = `${next.absent ? 1 : 0}-${next.mode}`;
      if (key === lastKey) return;
      lastKey = key;
      rowsState = [];
      paint();
    },
  };
  control.node.append(node);
  control.update(draft);
  return control;
}

// --------------------------------------------------------------- dışa aktar

export function exportTab(app, exam) {
  const node = h('div', 'ak-stack');
  const state = { kind: 'results_table', format: 'xlsx', classGroupId: '', students: [] };
  const kindSelect = selectBox([
    { value: 'results_table', label: 'Sonuç tablosu' },
    { value: 'report_cards', label: 'Öğrenci karneleri' },
  ], state.kind, (value) => {
    state.kind = value;
    if (value === 'report_cards' && state.format === 'xlsx') { state.format = 'pdf'; formatSelect.set('pdf'); }
  }, { label: 'İçerik' });
  const formatSelect = selectBox([{ value: 'xlsx', label: 'Excel (XLSX)' }, { value: 'pdf', label: 'PDF' }],
    state.format, (value) => { state.format = value; }, { label: 'Biçim' });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => { state.classGroupId = value; },
    { label: 'Sınıf' });
  examClassOptions(app, exam).then((options) => classSelect.options(options)).catch(() => {});

  const chosen = h('div', 'ak-chips');
  const paintChosen = () => {
    chosen.replaceChildren();
    if (!state.students.length) chosen.append(h('span', 'ak-muted', 'Öğrenci seçilmedi: süzgece uyan herkes.'));
    for (const student of state.students) {
      const chip = h('button', 'kit-chip on', `${student.displayName} ✕`);
      chip.type = 'button';
      chip.title = 'Listeden çıkar';
      chip.addEventListener('click', () => {
        state.students = state.students.filter((item) => item.id !== student.id);
        paintChosen();
      });
      chosen.append(chip);
    }
  };
  const picker = studentPicker(app, { placeholder: 'Belirli öğrenciler (isteğe bağlı)', onPick: (student) => {
    if (!state.students.some((item) => item.id === student.id)) state.students.push(student);
    paintChosen();
  } });
  paintChosen();

  const go = actionButton(app, 'admin_exportExam', 'Dosyayı hazırla', { variant: 'primary', onClick: async () => {
    go.disabled = true;
    try {
      await exportJob(app, () => app.api.post(`/v1/admin/exams/${exam.id}/exports`, {
        kind: state.kind,
        format: state.format,
        classGroupId: state.classGroupId || null,
        studentIds: state.students.length ? state.students.map((item) => item.id) : undefined,
      }, { key: newKey('exam-export') }), { name: `${slug(exam.name)}-${state.kind === 'report_cards' ? 'karneler' : 'sonuclar'}`,
        label: state.kind === 'report_cards' ? 'Karneler hazırlanıyor…' : 'Sonuç tablosu hazırlanıyor…' });
    } finally {
      go.disabled = false;
    }
  } });

  const form = h('div', 'ak-stack ak-card-pad');
  form.append(toolbar(kindSelect.node, formatSelect.node, classSelect.node), picker.node, chosen,
    h('div', 'ak-muted', 'Dosya Akademi\'de hazırlanır; hazır olunca iner (PDF yeni pencerede açılır). '
      + 'Yayımlanmamış sonuçlar da dosyaya girer.'),
    toolbar(go));
  node.append(card('Dışa aktar', form));
  return { node, reload: () => {}, destroy() { picker.destroy(); } };
}

function slug(text) {
  return String(text || 'deneme').toLocaleLowerCase('tr-TR')
    .replace(/[çğıöşü]/g, (ch) => ({ ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u' }[ch]))
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'deneme';
}
