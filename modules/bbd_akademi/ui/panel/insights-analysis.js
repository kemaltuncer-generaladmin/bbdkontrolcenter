// BBD Akademi — Erken uyarı › Konu ve hata analizi (F-A22; F-S18 kurum analizi).
//
// Sınıf / grup / ders ve dönem süzgeciyle iki görünüm:
//  · Hata defteri analizi (`admin_getMistakeInsights`): en çok hata yapılan
//    konular, hata nedenleri ve derslere dağılım — tek istek.
//  · Konu doğruluğu (`admin_getTopicAccuracyInsights`): en az 20 soru
//    çözülmüş konular, doğruluğa göre artan — imleçli liste.
// İkisi de Akademi'nin ön hesaplı günlük özetlerinden okunur; süzgeç
// değişince ikisi paralel tazelenir. Dönem en çok 180 gündür (sözleşme).
//
// Zayıf konular için plan görevi Çalışma planı bölümünde atanır.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { barChart } from '../../ui-kit/charts.js';
import { dateRange } from '../../ui-kit/datefield.js';
import {
  empty, fmtDate, fmtDateTime, loader, num, pagedList, percent, selectBox, todayIst, toolbar,
} from './common.js';
import { MISTAKE_REASONS } from './insights-shared.js';

const MAX_DAYS = 180;

const dayCount = (start, end) => {
  const [a, b] = [start, end].map((iso) => {
    const [year, month, day] = String(iso).split('-').map(Number);
    return Date.UTC(year, month - 1, day);
  });
  return Math.round((b - a) / 86_400_000) + 1;
};

const accuracyTone = (value) => {
  if (value === null || value === undefined) return 'dim';
  if (value < 50) return 'bad';
  if (value < 70) return 'warn';
  return 'good';
};

export function analysisTab(app) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', studentGroupId: '', subject: '' };
  let lookupsLoaded = false;
  const canMistakes = app.can('admin_getMistakeInsights');
  const canTopics = app.can('admin_getTopicAccuracyInsights');

  const range = dateRange({ start: todayIst(-29), end: todayIst(), label: 'Dönem', onChange: () => reload() });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '',
    (value) => { filters.classGroupId = value; reload(); }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '',
    (value) => { filters.studentGroupId = value; reload(); }, { label: 'Öğrenci grubu' });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '',
    (value) => { filters.subject = value; reload(); }, { label: 'Ders' });
  const rangeWarn = h('div');

  const planLink = app.can('admin_createPlanAssignment')
    ? button('Zayıf konulara plan görevi ata', { variant: 'ghost',
      onClick: () => app.go('plan', { tab: 'assignments' }) })
    : null;

  const query = () => {
    const { start, end } = range.get();
    return {
      classGroupId: filters.classGroupId || undefined,
      studentGroupId: filters.studentGroupId || undefined,
      subject: filters.subject || undefined,
      from: start || undefined,
      to: end || undefined,
    };
  };

  // ------------------------------------------------------- hata defteri
  const mistakes = loader(() => app.api.get('/v1/admin/insights/mistakes', query()), (data) => {
    const nodes = [];
    nodes.push(kpiRow([
      { label: 'Toplam hata kaydı', value: num(data.totalMistakes) },
      { label: 'Hata yazan öğrenci', value: num(data.studentCount) },
      { label: 'Baskın neden', value: data.dominantReason?.reason
        ? `${MISTAKE_REASONS[data.dominantReason.reason] || data.dominantReason.reason} `
          + `(${percent(data.dominantReason.percent, 0)})` : '—' },
    ]));
    nodes.push(h('div', 'ak-muted ak-in-note', `Dönem ${fmtDate(data.from)} – ${fmtDate(data.to)} · `
      + `hesaplama ${fmtDateTime(data.computedAt)}`));
    if (!data.totalMistakes) {
      nodes.push(empty('Bu dönemde hata kaydı yok',
        'Süzgece uyan öğrenciler hata defterine kayıt girmemiş. Dönemi genişletmeyi deneyin.'));
      return nodes;
    }
    const reasons = [...(data.byReason || [])].sort((a, b) => b.count - a.count)
      .map((row) => ({ label: MISTAKE_REASONS[row.reason] || row.reason, value: row.count,
        display: `${num(row.count)} · ${percent(row.percent, 0)}` }));
    const subjects = [...(data.bySubject || [])].sort((a, b) => b.count - a.count)
      .map((row) => ({ label: row.subject?.name || '—', value: row.count, display: num(row.count) }));
    const grid = h('div', 'ak-grid-2');
    grid.append(card('Hata nedenleri', barChart(reasons, { max: 7 }), 'Öğrencinin deftere yazdığı neden'),
      card('Derslere göre hata', barChart(subjects, { max: 10 })));
    nodes.push(grid);
    const topics = dataTable({
      columns: [
        { key: 'topic', label: 'Konu', width: 'minmax(0, 1.6fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('b', '', row.topic?.name || '—'), h('small', '', row.subject?.name || ''));
          return cell;
        } },
        { key: 'count', label: 'Hata', align: 'num', width: '80px', cell: (row) => num(row.count) },
        { key: 'studentCount', label: 'Öğrenci', align: 'num', width: '90px', cell: (row) => num(row.studentCount) },
        { key: 'dominantReason', label: 'Baskın neden', width: 'minmax(0, 1fr)',
          cell: (row) => (row.dominantReason ? MISTAKE_REASONS[row.dominantReason] || row.dominantReason : '—') },
      ],
      rows: data.topTopics || [],
      rowKey: (row) => row.topic?.id || '',
      dense: true,
      empty: empty('Konu bağlı hata yok', 'Hatalar konu seçilmeden girilmiş.'),
    });
    nodes.push(card('En çok hata yapılan konular', topics.node, 'Sınıfın en çok zorlandığı konular'));
    return nodes;
  }, { rows: 4, columns: 4 });

  // ------------------------------------------------------- konu doğruluğu
  const topicList = pagedList({
    app,
    path: '/v1/admin/insights/topics',
    query,
    limit: 50,
    rowKey: (row) => row.topic?.id || '',
    columns: [
      { key: 'topic', label: 'Konu', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.topic?.name || '—'), h('small', '', row.subject?.name || ''));
        return cell;
      } },
      { key: 'accuracy', label: 'Doğruluk', width: 'minmax(150px, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-in-meter');
        const bar = h('span', 'ak-in-meter-bar');
        const fill = h('span', `ak-in-meter-fill ${accuracyTone(row.accuracyPercent)}`);
        fill.style.width = `${Math.max(0, Math.min(100, Number(row.accuracyPercent) || 0))}%`;
        bar.append(fill);
        cell.append(bar, badge(percent(row.accuracyPercent, 0), accuracyTone(row.accuracyPercent)));
        return cell;
      } },
      { key: 'questions', label: 'Soru', align: 'num', width: '80px', cell: (row) => num(row.questions) },
      { key: 'studentCount', label: 'Öğrenci', align: 'num', width: '84px', cell: (row) => num(row.studentCount) },
      { key: 'struggling', label: 'Zorlanan', align: 'num', width: '90px',
        cell: (row) => (row.strugglingStudentCount
          ? badge(num(row.strugglingStudentCount), 'warn') : num(row.strugglingStudentCount)) },
    ],
    empty: { title: 'Yeterli veri yok',
      text: 'Konu doğruluğu en az 20 soru çözülmüş konular için hesaplanır. Dönemi genişletmeyi deneyin.' },
  });

  const parts = [];
  if (canMistakes) parts.push(card('Hata defteri analizi', h('div', 'ak-card-pad ak-stack')));
  if (canTopics) {
    parts.push(card('Konu doğruluğu', h('div', 'ak-card-pad ak-stack'),
      'En zayıf konular önce · en az 20 soru'));
  }
  node.append(toolbar(classSelect.node, groupSelect.node, subjectSelect.node, planLink), range.node, rangeWarn,
    ...parts);
  if (canMistakes) parts[0].querySelector('.ak-card-pad').append(mistakes.node);
  if (canTopics) parts[parts.length - 1].querySelector('.ak-card-pad').append(topicList.node);

  function loadLookups() {
    if (lookupsLoaded) return;
    lookupsLoaded = true;
    Promise.all([
      app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).catch(() => null),
      app.lookups.options('studentGroups', { empty: 'Tüm gruplar' }).catch(() => null),
      app.lookups.subjects().catch(() => null),
    ]).then(([classes, groups, subjects]) => {
      if (classes) classSelect.options(classes);
      if (groups) groupSelect.options(groups);
      if (subjects) {
        subjectSelect.options([{ value: '', label: 'Tüm dersler' },
          ...subjects.map((item) => ({ value: item.id, label: item.name }))]);
      }
    });
  }

  function reload() {
    loadLookups();
    const { start, end } = range.get();
    rangeWarn.replaceChildren();
    if (start && end && (end < start || dayCount(start, end) > MAX_DAYS)) {
      rangeWarn.append(alertBox(end < start ? 'Dönem bitişi başlangıçtan önce olamaz.'
        : `Dönem en çok ${MAX_DAYS} gün olabilir; aralığı daraltın.`, 'warn'));
      return;
    }
    if (canMistakes) mistakes.reload();
    if (canTopics) topicList.reload();
  }

  return {
    node,
    reload,
    destroy() {
      range.destroy();
      topicList.destroy();
    },
  };
}
