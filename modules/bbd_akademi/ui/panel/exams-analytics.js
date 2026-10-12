// BBD Akademi — Denemeler › analiz (F-E7).
//
// İki ekran: (1) tek deneme analizi — katılım, ortalama/medyan net, ders
// ortalamaları, sınıf ortalamaları, net dağılımı, sıralama ve en çok yanlış
// yapılan konular; (2) çoklu deneme analizi — bir deneme türünde sınıf/kurum
// net trendi, en zayıf konular ve 2–10 öğrencinin yan yana karşılaştırması.
// Bütün sayılar Akademi'de hesaplanır; burada yalnız çizilir.
//
// Grafikler kitten (charts.js). `paretoChart` kullanılmadı: ekseni para
// biçimindedir (₺) ve yanlış sayısını yanlış okuturdu; aynı bilgi sıralı
// yatay çubukta (`barChart`) net okunur.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { barChart, groupedBar, lineChart } from '../../ui-kit/charts.js';
import {
  empty, fmtDate, loader, num, optionsOf, selectBox, studentPicker, todayIst, toolbar,
} from './common.js';
import { EXAM_TYPES, netText, ratioText, sectionLabel, studentCell } from './exams-shared.js';

const netFormat = (value) => num(value, 1);
const shortDate = (iso) => {
  const [, month, day] = String(iso || '').split('-');
  return day && month ? `${day}.${month}` : String(iso || '');
};

// ------------------------------------------------------------ tek deneme

export function analysisTab(app, exam) {
  const node = h('div', 'ak-stack');
  let classGroupId = '';
  const classSelect = selectBox([{ value: '', label: 'Tüm kurum' }], '', (value) => {
    classGroupId = value; view.reload();
  }, { label: 'Sınıf' });
  app.lookups.classGroups().then((groups) => {
    const allowed = new Set(exam.classGroupIds || []);
    const list = allowed.size ? groups.filter((item) => allowed.has(item.id)) : groups;
    classSelect.options([{ value: '', label: 'Tüm kurum' }, ...list.map((item) => ({ value: item.id, label: item.name }))]);
  }).catch(() => {});
  node.append(toolbar(classSelect.node, h('span', 'kit-spacer'),
    exam.status !== 'published' ? h('span', 'ak-muted', 'Yayımlanmamış sonuçlar yalnız yöneticilere görünür.') : null));

  const view = loader(() => app.api.get(`/v1/admin/exams/${exam.id}/analysis`,
    { classGroupId: classGroupId || undefined }), (data) => render(data), { rows: 6, columns: 4 });
  node.append(view.node);

  function render(data) {
    if (!data?.participantCount) {
      return empty('Analiz için sonuç yok', 'Sonuçlar girildikçe ortalama, dağılım ve konu analizi burada görünür.');
    }
    const nodes = [kpiRow([
      { label: 'Katılan', value: num(data.participantCount) },
      { label: 'Girmeyen', value: num(data.absentCount), tone: data.absentCount ? 'warn' : '' },
      { label: 'Ortalama net', value: netText(data.averageNet) },
      { label: 'Medyan net', value: netText(data.medianNet) },
    ])];

    const sections = data.sections || [];
    const top = h('div', 'ak-grid-2');
    const defs = new Map((exam.sections || []).map((item) => [item.id, item]));
    top.append(card('Ders ortalamaları', chartBox(groupedBar(sections.map((item) => ({
      label: sectionLabel(defs.get(item.sectionId) || { subject: item.subject }),
      values: [Number(item.averageNet) || 0, Number(item.maxNet) || 0],
    })), { series: ['Ortalama net', 'En yüksek net'], format: netFormat }))));
    const distribution = data.distribution || [];
    top.append(card('Net dağılımı', chartBox(groupedBar(distribution.map((item) => ({
      label: `${num(item.fromNet)}–${num(item.toNet)}`,
      values: [item.count],
    })), { series: ['Öğrenci'], format: (value) => num(value) }))));
    nodes.push(top);

    const middle = h('div', 'ak-grid-2');
    const classes = data.classGroups || [];
    middle.append(card('Sınıf ortalamaları', classes.length ? chartBox(barChart([...classes]
      .sort((a, b) => (Number(b.averageNet) || 0) - (Number(a.averageNet) || 0))
      .map((item) => ({ label: item.classGroup?.name || '—', value: Number(item.averageNet) || 0,
        display: `${netText(item.averageNet)} net · ${num(item.studentCount)} öğr.` }))))
      : empty('Sınıf kırılımı yok')));
    const topics = data.topMissedTopics || [];
    middle.append(card('En çok yanlış yapılan konular', topics.length ? chartBox(barChart(topics.map((item) => ({
      label: item.topic?.name || '—',
      value: Number(item.errorRate) || 0,
      display: `${ratioText(item.errorRate)} · ${num(item.wrongCount)} yanlış`,
      tone: '#e2774a',
    }))))
      : empty('Konu analizi yok', 'Soru → konu eşlemesi girilince en çok yanlış yapılan konular burada görünür.')));
    nodes.push(middle);

    if (topics.length) {
      nodes.push(card('Konu ayrıntısı', dataTable({
        columns: [
          { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => row.topic?.name || '—' },
          { key: 'rate', label: 'Hata oranı', align: 'num', cell: (row) => ratioText(row.errorRate) },
          { key: 'wrong', label: 'Yanlış', align: 'num', cell: (row) => num(row.wrongCount) },
          { key: 'questions', label: 'Sorular', width: 'minmax(0, 1.2fr)',
            cell: (row) => (row.questionNos || []).join(', ') || '—' },
        ],
        rows: topics,
        dense: true,
      }).node));
    }

    const ranking = data.ranking || [];
    nodes.push(card(`Sıralama (${num(ranking.length)})`, dataTable({
      columns: [
        { key: 'rank', label: 'Sıra', width: '60px', align: 'num', cell: (row) => num(row.rank) },
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => studentCell(row.student) },
        { key: 'net', label: 'Toplam net', align: 'num', cell: (row) => netText(row.totalNet) },
        { key: 'national', label: 'Yayın TR sırası', align: 'num',
          cell: (row) => (row.publisherNationalRank ? num(row.publisherNationalRank) : '—') },
      ],
      rows: ranking,
      dense: true,
      empty: empty('Sıralama yok'),
    }).node));
    return nodes;
  }

  return { node, reload: () => view.reload(), destroy() {} };
}

function chartBox(svg) {
  const box = h('div', 'ak-ex-chart');
  box.append(svg);
  return box;
}

// ---------------------------------------------------------- çoklu deneme

export function analyticsPanel(app, { openExam } = {}) {
  const node = h('div', 'ak-stack');
  const filters = { type: 'tyt', classGroupId: '', from: todayIst(-365), to: todayIst(), includeHistorical: true };
  const typeSelect = selectBox(optionsOf(EXAM_TYPES), filters.type, (value) => {
    filters.type = value; view.reload();
  }, { label: 'Deneme türü' });
  const classSelect = selectBox([{ value: '', label: 'Tüm kurum' }], '', (value) => {
    filters.classGroupId = value; view.reload();
  }, { label: 'Sınıf' });
  app.lookups.options('classGroups', { empty: 'Tüm kurum' }).then((options) => classSelect.options(options)).catch(() => {});
  const range = dateRange({ start: filters.from, end: filters.to, label: 'Dönem', onChange: (value) => {
    filters.from = value.start || filters.from;
    filters.to = value.end || filters.to;
    view.reload();
  } });
  const historical = h('label', 'ak-inline ak-ex-toggle');
  const historicalCheck = h('input', 'kit-check');
  historicalCheck.type = 'checkbox';
  historicalCheck.checked = true;
  historicalCheck.addEventListener('change', () => { filters.includeHistorical = historicalCheck.checked; view.reload(); });
  historical.append(historicalCheck, h('span', '', 'Geçmiş kayıtları kat'));
  node.append(toolbar(typeSelect.node, classSelect.node, historical), toolbar(range.node));

  const view = loader(() => app.api.get('/v1/admin/exam-analytics', {
    type: filters.type,
    classGroupId: filters.classGroupId || undefined,
    from: filters.from,
    to: filters.to,
    includeHistorical: filters.includeHistorical,
  }), (data) => render(data), { rows: 6, columns: 4 });
  node.append(view.node);

  function render(data) {
    const exams = [...(data?.exams || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    if (!exams.length) {
      return empty('Bu süzgece uyan deneme yok', 'Türü, sınıfı ya da dönemi değiştirin. Geçmiş denemeler '
        + '“Geçmiş deneme ekle” ile girilebilir.');
    }
    const nodes = [];
    const averages = exams.map((item) => Number(item.averageNet)).filter((value) => Number.isFinite(value));
    const last = exams[exams.length - 1];
    nodes.push(kpiRow([
      { label: 'Deneme', value: num(exams.length) },
      { label: 'Son ortalama net', value: netText(last.averageNet), spark: averages,
        sparkTitle: 'Denemeler boyunca ortalama net' },
      { label: 'En yüksek ortalama', value: averages.length ? netText(Math.max(...averages)) : '—' },
      { label: 'Son katılım', value: num(last.participantCount) },
    ]));
    nodes.push(card('Ortalama net trendi', chartBox(lineChart(exams.map((item) => ({
      label: shortDate(item.date), value: Number(item.averageNet) || 0,
    })), { valueFormat: netFormat }))));

    const classes = data.classGroups || [];
    if (classes.length) {
      // Sınıf × deneme tablosu; az sınıf/deneme varsa gruplu çubuk da çizilir.
      const columns = [{ key: 'class', label: 'Sınıf', width: 'minmax(120px, 1.2fr)',
        cell: (row) => row.classGroup?.name || '—' }];
      for (const item of exams) {
        columns.push({ key: item.examId, label: shortDate(item.date), title: item.name, align: 'num', width: '72px',
          cell: (row) => netText((row.points || []).find((point) => point.examId === item.examId)?.averageNet) });
      }
      const parts = [];
      if (classes.length <= 6 && exams.length <= 12) {
        parts.push(chartBox(groupedBar(exams.map((item) => ({
          label: shortDate(item.date),
          values: classes.map((row) => Number((row.points || []).find((point) => point.examId === item.examId)
            ?.averageNet) || 0),
        })), { series: classes.map((row) => row.classGroup?.name || '—'), format: netFormat })));
      }
      parts.push(dataTable({ columns, rows: classes, dense: true }).node);
      const wrap = h('div', 'ak-stack');
      wrap.append(...parts);
      nodes.push(card('Sınıf ortalamaları', wrap));
    }

    const weak = data.weakTopics || [];
    const lower = h('div', 'ak-grid-2');
    lower.append(card('En zayıf konular', weak.length ? chartBox(barChart(weak.map((item) => ({
      label: item.topic?.name || '—',
      value: Number(item.errorRate) || 0,
      display: `${ratioText(item.errorRate)} · ${num(item.wrong)}Y ${num(item.blank)}B`,
      tone: '#e2774a',
    })))) : empty('Konu analizi yok', 'Denemelere soru → konu eşlemesi girilince zayıf konular görünür.')));
    lower.append(card('Denemeler', dataTable({
      columns: [
        { key: 'date', label: 'Tarih', width: '92px', cell: (row) => fmtDate(row.date) },
        { key: 'name', label: 'Deneme', width: 'minmax(0, 2fr)', cell: (row) => {
          const cell = h('span', 'ak-ex-name');
          cell.append(h('span', '', row.name));
          if (row.isHistorical) cell.append(badge('Geçmiş', 'dim'));
          return cell;
        } },
        { key: 'count', label: 'Katılım', align: 'num', cell: (row) => num(row.participantCount) },
        { key: 'avg', label: 'Ort. net', align: 'num', cell: (row) => netText(row.averageNet) },
      ],
      rows: [...exams].reverse(),
      dense: true,
      onRow: openExam ? (row) => openExam(row.examId) : undefined,
    }).node));
    nodes.push(lower);

    if (weak.length) {
      nodes.push(card('Zayıf konu ayrıntısı', dataTable({
        columns: [
          { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => row.topic?.name || '—' },
          { key: 'rate', label: 'Hata oranı', align: 'num', cell: (row) => ratioText(row.errorRate),
            title: '(yanlış + boş) / soru' },
          { key: 'wrong', label: 'Yanlış', align: 'num', cell: (row) => num(row.wrong) },
          { key: 'blank', label: 'Boş', align: 'num', cell: (row) => num(row.blank) },
          { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questionCount) },
          { key: 'exams', label: 'Deneme', align: 'num', cell: (row) => (row.examCount == null ? '—' : num(row.examCount)) },
          { key: 'review', label: 'Tekrar listesi', cell: (row) => (row.inReviewQueue ? badge('Listede', 'info') : '—') },
        ],
        rows: weak,
        dense: true,
      }).node));
    }
    return nodes;
  }

  const compare = compareCard(app, filters);
  node.append(compare.node);

  return {
    node,
    reload: () => view.reload(),
    destroy() { range.destroy(); compare.destroy(); },
  };
}

// ------------------------------------------------------- öğrenci karşılaştırma

function compareCard(app, filters) {
  const content = h('div', 'ak-stack ak-card-pad');
  const box = card('Öğrenci karşılaştırma', content, '2–10 öğrenci, seçili tür ve dönem');
  const chosen = [];
  const chips = h('div', 'ak-chips');
  const result = h('div', 'ak-stack');
  const go = button('Karşılaştır', { variant: 'primary', onClick: () => run() });

  const paintChips = () => {
    chips.replaceChildren();
    if (!chosen.length) chips.append(h('span', 'ak-muted', 'Öğrenci seçin (en az 2, en çok 10).'));
    for (const student of chosen) {
      const chip = h('button', 'kit-chip on', `${student.displayName} ✕`);
      chip.type = 'button';
      chip.title = 'Karşılaştırmadan çıkar';
      chip.addEventListener('click', () => {
        chosen.splice(chosen.findIndex((item) => item.id === student.id), 1);
        paintChips();
      });
      chips.append(chip);
    }
    go.disabled = chosen.length < 2;
  };
  const picker = studentPicker(app, { placeholder: 'Öğrenci ekle (ad, okul no)', onPick: (student) => {
    if (chosen.some((item) => item.id === student.id)) return;
    if (chosen.length >= 10) { app.toast('En çok 10 öğrenci karşılaştırılabilir.', 'warn'); return; }
    chosen.push(student);
    paintChips();
  } });
  content.append(toolbar(picker.node, h('span', 'kit-spacer'), go), chips, result);
  paintChips();

  async function run() {
    if (chosen.length < 2) return;
    go.disabled = true;
    const view = loader(() => app.api.get('/v1/admin/exam-analytics/compare', {
      type: filters.type,
      studentIds: chosen.map((item) => item.id),
      from: filters.from,
      to: filters.to,
    }), (data) => renderCompare(data), { rows: 4, columns: 4 });
    result.replaceChildren(view.node);
    await view.reload();
    go.disabled = chosen.length < 2;
  }

  function renderCompare(data) {
    const students = data?.students || [];
    const dates = [...new Set(students.flatMap((row) => (row.points || []).map((point) => `${point.date}|${point.examId}`)))]
      .sort();
    if (!students.length || !dates.length) {
      return empty('Karşılaştırılacak sonuç yok', `${EXAM_TYPES[filters.type] || filters.type} türünde bu dönemde `
        + 'seçilen öğrencilerin sonucu bulunmuyor.');
    }
    const valueAt = (row, key) => {
      const [, examId] = key.split('|');
      return (row.points || []).find((point) => point.examId === examId)?.totalNet ?? null;
    };
    const nodes = [h('div', 'ak-muted', `${EXAM_TYPES[data.examType] || data.examType} · ${fmtDate(filters.from)} – ${fmtDate(filters.to)}`)];
    if (dates.length <= 12) {
      nodes.push(chartBox(groupedBar(dates.map((key) => ({
        label: shortDate(key.split('|')[0]),
        values: students.map((row) => Number(valueAt(row, key)) || 0),
      })), { series: students.map((row) => row.student?.displayName || '—'), format: netFormat })));
    } else {
      nodes.push(alertBox('Deneme sayısı çok olduğu için yalnız tablo gösteriliyor; dönemi daraltın.', 'info'));
    }
    const columns = [
      { key: 'student', label: 'Öğrenci', width: 'minmax(160px, 1.4fr)', cell: (row) => studentCell(row.student) },
      { key: 'avg', label: 'Ortalama', align: 'num', width: '84px', cell: (row) => h('b', '', netText(row.averageNet)) },
      ...dates.map((key) => ({ key, label: shortDate(key.split('|')[0]), align: 'num', width: '70px',
        cell: (row) => netText(valueAt(row, key)) })),
    ];
    nodes.push(dataTable({ columns, rows: students, dense: true }).node);
    return nodes;
  }

  return { node: box, destroy() { picker.destroy(); } };
}
