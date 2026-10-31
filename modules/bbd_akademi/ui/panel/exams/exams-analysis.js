// BBD Akademi — deneme sonucu: tam ayrıntılı analiz + "Karne (PDF)" (Akademi README §14 madde 138).
//
// Kullanıcı (26.10.2026): "tüm yanlışlar olsun ayrıntılı bir analiz istiyoruz sadece ilk
// 10 konu fln değil tam ayrıntılı". Tek bileşen, iki yer: öğrenci → Denemeler → deneme
// çekmecesi (`students-records.js`) ve Denemeler → Sonuçlar → satırın "Analiz"i
// (`exams-results.js`).
//
// Veri tek istek: `students_getExamResultAnalysis` (soru soru tablo + bütün konular).
// Süzgeç ve ders seçimi İSTEMCİDE (yeni istek atılmaz). Karne: `students_getExamReportCard`
// → kısa ömürlü imzalı bağlantı → `fetchFile` → PDF yeni pencerede. Öğrencinin / velinin
// "Karnemi indir"iyle AYNI belge (KM'nin "Dışa aktar → Öğrenci karneleri" ile aynı içerik).
// Mantık ve metinler `exams-analysis-logic.js` (import'suz, Node testli).

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { openBlob } from '../api.js';
import { empty, loader, problem } from '../common.js';
import {
  FILTERS, RESULT_LABELS, TEXT, answerText, filterCounts, filterQuestions, outcomeText, questionTitle,
  ratePct, resultOf, subjectHeading, subjectOptions, topicGroups, topicText, unmatchedText,
} from './exams-analysis-logic.js';

const OP_ANALYSIS = 'students_getExamResultAnalysis';
const OP_REPORT_CARD = 'students_getExamReportCard';
const OP_ANALYSIS_PDF = 'admin_getStudentExamAnalysisPdf';

/**
 * "Karne (PDF)" düğmesi: Akademi PDF'i hazırlar, imzalı bağlantıdan indirilir ve açılır.
 * Yetki yoksa gerekçeli kapalı düğme; sonuç yoksa (`absent`) çağıran düğmeyi hiç koymaz.
 */
export function reportCardButton(app, { studentId, examId, label = TEXT.reportCard, variant = '' }) {
  if (!app.api.can(OP_REPORT_CARD)) {
    const blocked = button(label, { variant, disabled: true });
    blocked.title = 'Bu işlem için yetkiniz yok (yöneticinizden izin isteyin).';
    return blocked;
  }
  const node = button(label, { variant, onClick: async () => {
    node.disabled = true;
    const before = node.textContent;
    node.textContent = TEXT.reportCardBusy;
    try {
      const out = await app.api.get(`/v1/students/${studentId}/exams/${examId}/report-card`);
      const file = await app.api.fetchFile(out.url, out.fileName || 'karne.pdf');
      openBlob(file);
    } catch (failure) {
      app.toast(problem(failure, TEXT.reportCardFailed), 'bad');
    } finally {
      node.textContent = before;
      node.disabled = false;
    }
  } });
  return node;
}

/**
 * "Soru soru analizi (PDF)" (iş 83): A4, en çok 2 sayfa — BÜTÜN sorular derse göre, cevap · doğru · sonuç · kazanım.
 * Yönetici ucu (`admin_getStudentExamAnalysisPdf`: kurum kapsamı yayından önce de alır); dosya imzalı bağlantıdan iner.
 */
export function analysisPdfButton(app, { studentId, examId, label = TEXT.analysisPdf, variant = '' }) {
  if (!app.api.can(OP_ANALYSIS_PDF)) {
    const blocked = button(label, { variant, disabled: true });
    blocked.title = app.api.operation?.(OP_ANALYSIS_PDF)
      ? 'Bu işlem için yetkiniz yok (yöneticinizden izin isteyin).'
      : 'Akademi sunucusu bu belgeyi henüz desteklemiyor.';
    return blocked;
  }
  const node = button(label, { variant, onClick: async () => {
    node.disabled = true;
    const before = node.textContent;
    node.textContent = TEXT.analysisPdfBusy;
    try {
      const out = await app.api.get(`/v1/admin/students/${studentId}/exams/${examId}/analysis-pdf`);
      const file = await app.api.fetchFile(out.url, out.fileName || 'soru-soru-analiz.pdf');
      openBlob(file);
    } catch (failure) {
      app.toast(problem(failure, TEXT.analysisPdfFailed), 'bad');
    } finally {
      node.textContent = before;
      node.disabled = false;
    }
  } });
  return node;
}

/**
 * Ayrıntılı analiz bölümü: ders seçimi + "Soru soru analiz" + "Konu analizi".
 * @returns {{node: HTMLElement, reload: () => Promise<void>}}
 */
export function examAnalysisSection(app, { studentId, examId }) {
  loadStyles(import.meta.url, 'exams.css'); // öğrenci çekmecesinden açılınca da (bir kez yüklenir)
  const node = h('div', 'ak-stack ak-ex-analysis');
  if (!app.api.can(OP_ANALYSIS)) {
    node.append(alertBox('Ayrıntılı analiz için yetkiniz yok.', 'warn'));
    return { node, reload: async () => {} };
  }
  const view = loader(() => app.api.get(`/v1/students/${studentId}/exams/${examId}/analysis`),
    (analysis) => render(analysis), { rows: 6, columns: 5 });
  node.append(view.node);

  function render(analysis) {
    if (analysis.absent) return [];
    // Soru soru (iş 83): varsayılan BÜTÜN sorular (doğrular dahil); süzgeç yanlış / boş'a daraltır.
    const state = { filter: 'all', group: '' };
    const sections = new Map((analysis.sections || []).map((section) => [section.id, section]));
    const questionsBody = h('div', 'ak-stack');
    const topicsBody = h('div', 'ak-stack');
    const nodes = [];

    const subjects = subjectOptions(analysis);
    if (subjects.length) {
      const chips = chipRow(subjects, '', (key) => {
        if (key === null) { chips.set(state.group); return; } // her zaman bir seçim
        state.group = key;
        paintQuestions();
        paintTopics();
      });
      chips.node.classList.add('ak-ex-chips');
      chips.node.setAttribute('aria-label', 'Ders seçimi');
      nodes.push(chips.node);
    }

    let filters = null;
    if (analysis.detail === 'questions') {
      filters = chipRow(FILTERS.map((item) => ({ ...item, count: 0 })), state.filter, (key) => {
        if (key === null) { filters.set(state.filter); return; }
        state.filter = key;
        paintQuestions();
      });
      filters.node.classList.add('ak-ex-chips');
      filters.node.setAttribute('aria-label', 'Soru süzgeci');
    }
    const questionsCard = card(TEXT.questionsTitle, h('div', 'ak-stack ak-card-pad'), TEXT.questionsHint);
    questionsCard.lastChild.append(...[filters?.node, questionsBody].filter(Boolean));
    const topicsCard = card(TEXT.topicsTitle, topicsBody, TEXT.topicsHint);
    nodes.push(questionsCard, topicsCard);

    function paintQuestions() {
      if (analysis.detail !== 'questions') {
        questionsBody.replaceChildren(empty(TEXT.countsOnlyTitle, TEXT.countsOnlyText));
        return;
      }
      filters.counts(filterCounts(analysis.questions, state.group));
      const rows = filterQuestions(analysis.questions, state.filter, state.group);
      const anyMistake = filterCounts(analysis.questions, state.group).wrong_blank > 0;
      const emptyTitle = state.filter === 'all' || anyMistake ? TEXT.noMatch : TEXT.noMistakes;
      questionsBody.replaceChildren(dataTable({
        dense: true,
        rowKey: (row) => `${row.sectionId}:${row.questionNo}`,
        columns: [
          { key: 'q', label: 'Soru', width: 'minmax(150px, 1.1fr)', cell: (row) => {
            const cell = h('div', 'ak-cell-stack');
            cell.append(h('span', '', questionTitle(row, sections, analysis.bookletType)),
              h('small', '', answerText(row)));
            return cell;
          } },
          { key: 'r', label: 'Durum', width: '78px', cell: (row) => {
            const [text, tone] = RESULT_LABELS[resultOf(row)];
            return badge(text, tone);
          } },
          { key: 't', label: 'Konu / kazanım', width: 'minmax(0, 2fr)', cell: (row) => {
            const cell = h('div', 'ak-cell-stack ak-ex-topic');
            cell.append(h('span', row.topics?.length ? '' : 'ak-muted', topicText(row)));
            const outcome = outcomeText(row);
            if (outcome) cell.append(h('small', '', outcome));
            return cell;
          } },
        ],
        rows,
        empty: empty(emptyTitle),
      }).node);
    }

    function paintTopics() {
      const groups = topicGroups(analysis, state.group);
      if (!groups.length) {
        topicsBody.replaceChildren(h('div', 'ak-card-pad', ''));
        topicsBody.firstChild.append(empty(TEXT.noTopicsTitle, TEXT.noTopicsText));
      } else {
        topicsBody.replaceChildren(...groups.map((group) => {
          const box = h('div', 'ak-ex-topic-group');
          box.append(h('h4', 'ak-ex-topic-head', subjectHeading(group.subject)), dataTable({
            dense: true,
            rowKey: (row) => `${row.topic?.id}:${row.unitId || ''}`,
            columns: [
              { key: 'n', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => {
                const cell = h('div', 'ak-ex-name');
                cell.append(h('span', '', row.topic?.name || '—'));
                if (row.weak) cell.append(badge(TEXT.weak, 'bad'));
                return cell;
              } },
              { key: 'q', label: 'Soru', align: 'num', width: '52px', cell: (row) => String(row.questionCount) },
              { key: 'c', label: 'D', align: 'num', width: '44px', cell: (row) => String(row.correct) },
              { key: 'w', label: 'Y', align: 'num', width: '44px', cell: (row) => String(row.wrong) },
              { key: 'b', label: 'B', align: 'num', width: '44px', cell: (row) => String(row.blank) },
              { key: 's', label: 'Başarı', align: 'num', width: '64px', cell: (row) => ratePct(row.successRate) },
              { key: 'cl', label: 'Sınıf', align: 'num', width: '58px', cell: (row) => ratePct(row.classSuccessRate) },
              { key: 'in', label: 'Kurum', align: 'num', width: '58px',
                cell: (row) => ratePct(row.institutionSuccessRate) },
            ],
            rows: group.topics,
            empty: empty(TEXT.noTopicsTitle),
          }).node);
          return box;
        }));
      }
      const note = unmatchedText(analysis.unmatchedQuestionCount || 0);
      if (note) topicsBody.append(h('div', 'ak-muted ak-ex-note ak-card-pad', note));
    }

    paintQuestions();
    paintTopics();
    return nodes;
  }

  return { node, reload: view.reload };
}
