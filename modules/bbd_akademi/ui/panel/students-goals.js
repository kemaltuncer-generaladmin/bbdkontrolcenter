// BBD Akademi — öğrenci detayı: Hedef sekmesi (salt okunur).
//
// Üç parça, üçü de Akademi'nin öğrenci uçlarından:
//   · Öğrenme özeti (`learning-overview`): plan uyumu, soru, hata defteri,
//     okuma, ajanda, bilgi kartı, hedef, değerlendirme, öz değerlendirme ve
//     açık erken uyarılar tek yükte — kurum öğrenci detayının özeti.
//   · Hedef net analizi (`goals/target-analysis`): hedef okul/puan → ders
//     başına önerilen net, son deneme neti ve açık. Hesap Akademi'dedir
//     (`modelVersion`, tahminse `isEstimate`).
//   · Puan hesaplayıcı (`score-calculator`): sınav modelleri (bölüm, soru
//     sayısı, kaç yanlış bir doğruyu götürür) ve son denemeden doldurulan
//     netler. Puan katsayıları sözleşmede YOK: panel yalnız NET hesaplar
//     (doğru − yanlış ÷ kural) ve puanı uydurmaz; Akademi'nin uyarı metni
//     (`disclaimer`) olduğu gibi yazılır.

import { h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { groupedBar } from '../../ui-kit/charts.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  empty, fmtDate, fmtDateTime, gradeText, kv, loader, net, num, percent, selectBox,
} from './common.js';

const SCORE_TYPES = { lgs: 'LGS', tyt: 'TYT', say: 'AYT Sayısal', ea: 'AYT Eşit ağırlık',
  soz: 'AYT Sözel', dil: 'AYT Dil' };
const BASIS = { target_score: 'Hedef puana göre', target_rank: 'Hedef sıralamaya göre',
  institution_suggestion: 'Kurum önerisi', none: 'Hedef tanımlı değil' };
const REASONS = { knowledge_gap: 'Bilgi eksiği', misconception: 'Kavram yanılgısı',
  calculation_error: 'İşlem hatası', careless: 'Dikkatsizlik', misread_question: 'Soruyu yanlış okuma',
  time_shortage: 'Süre yetmedi', guess: 'Tahmin' };
const SEVERITY = { high: ['Yüksek', 'bad'], medium: ['Orta', 'warn'], low: ['Düşük', 'info'] };
const AGENDA = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik ders', homework: 'Ödev',
  exam: 'Deneme', plan_task: 'Plan görevi', meeting: 'Görüşme', leave: 'İzin',
  institution_event: 'Kurum etkinliği', personal_event: 'Kişisel', todo: 'Yapılacak' };

const pct = (value) => (value === null || value === undefined ? '—' : percent(value, 0));

/**
 * @returns {{node: HTMLElement, shown: Function, destroy: Function}}
 */
export function goalsPanel(app, studentId) {
  loadStyles(import.meta.url, 'students-goals.css');
  const node = h('div', 'ak-stack ak-sgl');
  const bodies = {};
  let started = false;

  const tabs = tabBar([
    { key: 'overview', label: 'Öğrenme özeti' },
    { key: 'target', label: 'Hedef net' },
    { key: 'calculator', label: 'Puan hesaplayıcı' },
  ], 'overview', (key) => show(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  const builders = {
    overview: () => overviewView(app, studentId),
    target: () => targetView(app, studentId),
    calculator: () => calculatorView(app, studentId),
  };

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  return {
    node,
    shown() {
      if (started) return;
      started = true;
      show('overview');
    },
    destroy() {},
  };
}

// ----------------------------------------------------------- öğrenme özeti

function overviewView(app, studentId) {
  return loader(() => app.api.get(`/v1/students/${studentId}/learning-overview`), (data) => {
    const nodes = [];
    const plan = data.plan || {};
    const questions = data.questions || {};
    const mistakes = data.mistakes || {};
    const reading = data.reading || {};
    const flash = data.flashcards || {};
    const goals = data.goals || {};
    nodes.push(kpiRow([
      { label: 'Plan uyumu (bu hafta)', value: pct(plan.thisWeekAdherencePercent),
        title: `Geçen hafta: ${pct(plan.lastWeekAdherencePercent)}` },
      { label: 'Bugünkü görev', value: `${num(plan.todayDone)} / ${num(plan.todayTotal)}` },
      { label: 'Soru (hafta / ay)', value: `${num(questions.week)} / ${num(questions.month)}`,
        title: `Bugün ${num(questions.today)} · doğruluk ${pct(questions.accuracyPercent)}` },
      { label: 'Hata defteri (aktif)', value: num(mistakes.activeCount),
        title: `Öğrenilen ${num(mistakes.learnedCount)} · bu hafta eklenen ${num(mistakes.addedThisWeek)}` },
      { label: 'Kart tekrarı (bugün)', value: num(flash.dueToday),
        title: `14 günde ${num(flash.reviewedLast14Days)} tekrar · istikrar ${pct(flash.consistencyPercent)}` },
    ]));

    const warnings = data.earlyWarnings || {};
    if (warnings.openCount) {
      const box = h('div', 'ak-stack ak-card-pad');
      for (const item of warnings.items || []) {
        const row = h('div', 'ak-inline');
        row.append(badge(...(SEVERITY[item.severity] || [item.severity, 'dim'])), h('span', '', item.title));
        box.append(row);
      }
      if (app.can('admin_listEarlyWarnings')) {
        const link = h('button', 'ak-link', 'Erken uyarılarda aç');
        link.type = 'button';
        link.addEventListener('click', () => app.go('insights', { studentId }));
        box.append(link);
      }
      nodes.push(card(`Açık erken uyarı (${num(warnings.openCount)})`, box));
    }

    const grid = h('div', 'ak-grid-2');
    grid.append(
      card('Plan uyumu — derslere göre', (plan.bySubject || []).length
        ? groupedBar(plan.bySubject.map((row) => ({ label: row.subject?.name || '—',
          values: [row.adherencePercent ?? 0] })), { series: ['Uyum %'], height: 180, format: (v) => `%${num(v)}` })
        : empty('Plan verisi yok'), plan.lastEncouragementAt
        ? `Son teşvik notu: ${fmtDateTime(plan.lastEncouragementAt)}` : ''),
      card('En zayıf konular (soru takibi)', listOf((questions.weakestTopics || []).map((row) =>
        `${row.topic?.name || '—'} — %${num(row.accuracyPercent)} (${num(row.questions)} soru)`),
      'Yeterli soru kaydı yok.')),
      card('Hata defteri', kv([
        ['Baskın sebep', mistakes.dominantReason?.reason
          ? `${REASONS[mistakes.dominantReason.reason] || mistakes.dominantReason.reason} (%${num(mistakes.dominantReason.percent)})`
          : '—'],
        ['En çok hata', (mistakes.topTopics || []).map((row) => `${row.topic?.name} (${num(row.count)})`).join(', ') || '—'],
        ['Görseller', mistakes.imagesVisible ? 'Kuruma açık' : 'Öğrenci gizlemiş'],
      ])),
      card('Okuma', kv([
        ['Okuduğu kitap', reading.currentBook ? `${reading.currentBook.title}${reading.currentBook.author
          ? ` — ${reading.currentBook.author}` : ''}${reading.currentBook.pageCount
          ? ` (${num(reading.currentBook.currentPage)} / ${num(reading.currentBook.pageCount)} sayfa)` : ''}` : '—'],
        ['Seri', reading.streak ? `${num(reading.streak.current)} gün (en iyi ${num(reading.streak.best)})` : '—'],
        ['Bu ay', `${num(reading.monthPages)} sayfa · ${num(reading.monthBooks)} kitap`],
      ])),
      card('Hedef', kv([
        ['Hedef okul', goals.targetSchool || '—'],
        ['Puan türü', SCORE_TYPES[goals.scoreType] || '—'],
        ['Son net / hedef net', `${net(goals.lastNet)} / ${net(goals.targetNet)}`],
        ['Açık', goals.gap === null || goals.gap === undefined ? '—' : net(goals.gap)],
      ])),
      card('Değerlendirme ve öz değerlendirme', kv([
        ['Son değerlendirme', data.evaluations?.latest
          ? `${data.evaluations.latest.title} · ${data.evaluations.latest.period?.name || ''} · ortalama `
            + `${net(data.evaluations.latest.average)}${data.evaluations.latest.previousAverage !== null
              && data.evaluations.latest.previousAverage !== undefined
              ? ` (önceki ${net(data.evaluations.latest.previousAverage)})` : ''}`
          : '—'],
        ['Son öz değerlendirme', data.selfReview
          ? `${fmtDate(data.selfReview.weekStart)} haftası · motivasyon ${num(data.selfReview.motivation)}/5 · `
            + `odak ${num(data.selfReview.focus)}/5`
          : '—'],
      ])),
    );
    nodes.push(grid);

    const upcoming = (data.agenda?.upcoming || []).slice(0, 12);
    nodes.push(card('Yaklaşan (ajanda)', upcoming.length ? dataTable({
      dense: true,
      columns: [
        { key: 'date', label: 'Tarih', width: '110px', cell: (row) => fmtDate(row.date) },
        { key: 'type', label: 'Tür', width: '130px', cell: (row) => AGENDA[row.type] || row.type },
        { key: 'title', label: 'Başlık', width: 'minmax(0, 2fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', row.title), h('small', '', row.subtitle || row.subject?.name || ''));
          return cell;
        } },
        { key: 'time', label: 'Saat', width: '110px', cell: (row) => (row.allDay ? 'Tüm gün'
          : [row.startsAt, row.endsAt].filter(Boolean).map((value) => fmtDateTime(value).slice(-5)).join('–') || '—') },
      ],
      rows: upcoming,
      rowKey: (row) => row.id,
    }).node : empty('Yaklaşan bir şey yok')));

    const student = data.student || {};
    nodes.push(h('div', 'ak-muted ak-sgl-note', `${student.displayName || ''} · ${gradeText(student.gradeLevel)} · `
      + `hesaplama ${fmtDateTime(data.computedAt)}`));
    return nodes;
  }, { rows: 4, columns: 5 });
}

function listOf(lines, emptyText) {
  if (!lines.length) return h('div', 'ak-card-pad ak-muted', emptyText);
  const list = h('ul', 'ak-sgl-list');
  for (const line of lines) list.append(h('li', '', line));
  return list;
}

// -------------------------------------------------------------- hedef net

function targetView(app, studentId) {
  const node = h('div', 'ak-stack');
  let scoreType = '';
  const select = selectBox([{ value: '', label: 'Öğrencinin hedefine göre' },
    ...Object.entries(SCORE_TYPES).map(([value, label]) => ({ value, label }))], '', (value) => {
    scoreType = value; view.reload();
  }, { label: 'Puan türü' });
  const view = loader(() => app.api.get(`/v1/students/${studentId}/goals/target-analysis`,
    { scoreType: scoreType || undefined }), (data) => {
    const nodes = [];
    if (data.isEstimate) nodes.push(alertBox(`${data.label || 'Tahmini değerler'} — model ${data.modelVersion}.`, 'info'));
    const school = data.targetSchool;
    nodes.push(card('', kv([
      ['Puan türü / deneme türü', `${SCORE_TYPES[data.scoreType] || data.scoreType} · ${data.examType}`],
      ['Hedef', school ? [school.name, school.department, school.targetScore !== null && school.targetScore !== undefined
        ? `${num(school.targetScore, 2)} puan` : '', school.targetRank ? `${num(school.targetRank)}. sıra` : '']
        .filter(Boolean).join(' · ') : 'Hedef okul tanımlı değil'],
      ['Dayanak', BASIS[data.basis] || data.basis],
      ['Son deneme', data.lastExam ? `${data.lastExam.name} · ${fmtDate(data.lastExam.date)} · toplam net `
        + `${net(data.lastExam.totalNet)}` : 'Bu türde deneme sonucu yok'],
      ['Toplam (önerilen / hedef / son)', `${net(data.totalSuggestedNet)} / ${net(data.totalTargetNet)} / `
        + `${net(data.totalLastNet)}`],
    ])));
    const sections = data.sections || [];
    if (!sections.length) {
      nodes.push(empty('Ders kırılımı yok', 'Hedef ya da deneme sonucu tanımlanınca ders başına netler burada görünür.'));
      return nodes;
    }
    nodes.push(card('Ders başına net', groupedBar(sections.map((row) => ({ label: row.subject?.name || '—',
      values: [row.targetNet ?? row.suggestedNet ?? 0, row.lastNet ?? 0] })), {
      series: ['Hedef net', 'Son net'], height: 200, format: (v) => num(v, 1) })));
    nodes.push(dataTable({
      columns: [
        { key: 'subject', label: 'Ders', width: 'minmax(0, 2fr)', cell: (row) => row.subject?.name || '—' },
        { key: 'q', label: 'Soru', align: 'num', cell: (row) => num(row.questionCount) },
        { key: 'suggested', label: 'Önerilen', align: 'num', cell: (row) => net(row.suggestedNet) },
        { key: 'target', label: 'Hedef', align: 'num', cell: (row) => net(row.targetNet) },
        { key: 'last', label: 'Son', align: 'num', cell: (row) => net(row.lastNet) },
        { key: 'gap', label: 'Açık', align: 'num', cell: (row) => (row.gap === null || row.gap === undefined ? '—'
          : badge(net(row.gap), row.gap > 0 ? 'warn' : 'good')) },
      ],
      rows: sections,
      rowKey: (row) => row.subject?.id,
    }).node);
    nodes.push(h('div', 'ak-muted ak-sgl-note', `Hesaplama ${fmtDateTime(data.computedAt)} · model ${data.modelVersion}`));
    return nodes;
  });
  node.append(select.node, view.node);
  return { node, reload: () => view.reload() };
}

// ---------------------------------------------------------- puan hesaplayıcı

function calculatorView(app, studentId) {
  return loader(() => app.api.getCached(`/v1/students/${studentId}/score-calculator`, undefined,
    { ttlMs: 5 * 60_000 }), (data) => {
    const models = data.models || [];
    if (!models.length) return empty('Puan modeli yok');
    const prefill = data.prefill || null;
    let active = prefill?.scoreType && models.some((model) => model.scoreType === prefill.scoreType)
      ? prefill.scoreType : models[0].scoreType;
    const wrap = h('div', 'ak-stack');
    const formSlot = h('div');
    const select = selectBox(models.map((model) => ({ value: model.scoreType, label: model.title })), active,
      (value) => { active = value; paint(); }, { label: 'Sınav modeli' });
    wrap.append(select.node, formSlot);
    if (data.disclaimer) wrap.append(alertBox(data.disclaimer, 'info'));

    function paint() {
      const model = models.find((item) => item.scoreType === active);
      const nets = new Map((prefill?.scoreType === active ? prefill.nets : []).map((row) => [row.key, row.net]));
      const state = new Map();
      const total = h('b', 'ak-sgl-total');
      const rows = model.sections.map((section) => {
        const row = h('div', 'ak-sgl-row');
        const correct = numberInput(`${section.title}: doğru`, section.questionCount);
        const wrong = numberInput(`${section.title}: yanlış`, section.questionCount);
        const result = h('span', 'ak-num ak-sgl-net');
        const prefilled = nets.get(section.key);
        state.set(section.key, { correct, wrong, result, section, prefilled });
        const update = () => { paintRow(section.key); paintTotal(); };
        correct.addEventListener('input', update);
        wrong.addEventListener('input', update);
        row.append(h('span', '', `${section.title} (${num(section.questionCount)} soru, `
          + `${section.wrongsPerCorrect} yanlış 1 doğru)`), correct, wrong, result);
        return row;
      });
      function paintRow(key) {
        const entry = state.get(key);
        const c = Number(entry.correct.value || 0);
        const w = Number(entry.wrong.value || 0);
        const typed = entry.correct.value !== '' || entry.wrong.value !== '';
        const over = c + w > entry.section.questionCount;
        const value = typed ? c - w / Number(entry.section.wrongsPerCorrect) : entry.prefilled;
        entry.value = over ? null : value ?? null;
        entry.result.textContent = over ? 'Soru sayısını aşıyor' : (value === undefined || value === null ? '—' : net(value));
        entry.result.classList.toggle('bad', over);
      }
      function paintTotal() {
        const values = [...state.values()].map((entry) => entry.value).filter((value) => value !== null && value !== undefined);
        total.textContent = values.length ? `Toplam net: ${net(values.reduce((sum, value) => sum + value, 0))}` : 'Toplam net: —';
      }
      for (const key of state.keys()) paintRow(key);
      paintTotal();
      const head = h('div', 'ak-sgl-row ak-sgl-headrow');
      head.append(h('span', '', 'Bölüm'), h('span', '', 'Doğru'), h('span', '', 'Yanlış'), h('span', '', 'Net'));
      const box = h('div', 'ak-stack ak-card-pad');
      if (prefill?.scoreType === active && prefill.exam) {
        box.append(h('div', 'ak-muted', `Son denemeden dolduruldu: ${prefill.exam.name} (${fmtDate(prefill.exam.date)}). `
          + 'Doğru/yanlış yazınca o bölümün neti yeniden hesaplanır.'));
      }
      box.append(head, ...rows, total);
      if (model.usesDiplomaGrade) box.append(h('div', 'ak-muted', 'Bu puan türünde diploma notu (OBP) da hesaba katılır.'));
      box.append(h('div', 'ak-muted', `En yüksek puan ${num(model.maxScore)} · model ${data.modelVersion}. `
        + 'Puan katsayıları Akademi\'dedir; bu ekran net hesaplar.'));
      formSlot.replaceChildren(card(model.title, box));
    }
    paint();
    return wrap;
  });
}

function numberInput(label, max) {
  const input = h('input', 'kit-input ak-sgl-input');
  input.type = 'number';
  input.min = '0';
  input.max = String(max);
  input.step = '1';
  input.inputMode = 'numeric';
  input.setAttribute('aria-label', label);
  return input;
}
