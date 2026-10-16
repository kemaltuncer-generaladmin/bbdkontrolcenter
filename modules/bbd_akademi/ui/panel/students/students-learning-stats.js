// BBD Akademi — öğrenci "Öğrenme" sekmesinin parçası: soru takibi ve okuma
// (F-S17, F-S19; F-A24) + `students-learning.js` ile ortak yardımcılar.
//
// Salt okunur; öğrenci uçlarından (`/v1/students/{id}/*`) okunur. Ön hesaplı
// özetler (`computedAt`) ekranda "özet … itibarıyla" diye yazılır.
//
// KAPALI ÖZELLİK: sınıf kıyası (`class_comparison`) ve okuma ligi
// (`reading_league`) kiracı bayrağıdır; kapalıysa uç `feature_disabled` döner,
// ekran hata değil sebep gösterir.
//
// GÖRSEL: gömülü imzalı bağlantı tazeyse doğrudan, değilse (ya da indirme
// düşerse) `students_getFile` ile taze bağlantı alınır; nesne URL'leri çekmece
// kapanınca bırakılır.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { barChart, lineChart } from '../../../ui-kit/charts.js';
import { AkademiError } from '../api.js';
import {
  addDays, dateInput, empty, fmtDate, fmtDateTime, kv, loader, num, openDrawer, pagedList, percent,
  problem, selectBox, statusBadge, todayIst, toolbar, weekStart,
} from '../common.js';

const MONTHS_SHORT = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
const MONTHS_LONG = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos',
  'Eylül', 'Ekim', 'Kasım', 'Aralık'];

const BANDS = { top_10: ['Sınıfın ilk %10’u', 'good'], top_25: ['Sınıfın ilk %25’i', 'good'],
  above_average: ['Sınıf ortalamasının üstünde', 'info'], below_average: ['Sınıf ortalamasının altında', 'warn'],
  bottom_25: ['Sınıfın son %25’i', 'bad'] };
const SHELF_STATUS = { reading: ['Okunuyor', 'info'], to_read: ['Okunacak', 'dim'], finished: ['Bitti', 'good'],
  abandoned: ['Yarım bırakıldı', 'warn'] };
const SHELF_SOURCE = { institution_list: 'Kurum listesi', catalog: 'Hazır katalog', isbn: 'ISBN (bbdstore)',
  manual: 'Elle eklendi' };
const SESSION_SOURCE = { timer: 'Zamanlayıcı', manual: 'Elle', plan_task: 'Plan görevi' };

const isFeatureOff = (failure) => failure instanceof AkademiError && failure.code === 'feature_disabled';
const monthLong = (month) => {
  const [year, index] = String(month).split('-').map(Number);
  return `${MONTHS_LONG[index - 1] || '?'} ${year}`;
};

// ------------------------------------------------------------ ortak yardımcılar

export const dayMonth = (iso) => fmtDate(iso).slice(0, 5);
export const minutes = (value) => `${num(value || 0)} dk`;
export const accuracyTone = (value) => (value === null || value === undefined ? 'dim'
  : value < 50 ? 'bad' : value < 70 ? 'warn' : 'good');
export const stack = (main, sub) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', main || '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
};
export const box = (...nodes) => {
  const node = h('div', 'ak-stack');
  node.append(...nodes.filter(Boolean));
  return node;
};

/**
 * Öğrenci görseli → nesne URL'si. Gömülü imzalı bağlantı tazeyse doğrudan,
 * değilse (ya da indirme düşerse) `students_getFile` ile taze bağlantı alınır.
 */
async function imageUrl(app, studentId, file) {
  if (!file?.id) return null;
  let blob = null;
  const fresh = file.url && file.urlExpiresAt && Date.parse(file.urlExpiresAt) - Date.now() > 30_000;
  if (fresh) {
    try { ({ blob } = await app.api.fetchFile(file.url, 'gorsel')); } catch { blob = null; }
  }
  if (!blob) {
    const info = await app.api.get(`/v1/students/${studentId}/files/${file.id}`);
    if (!info?.url) throw new AkademiError({ code: 'hidden_by_student', message: 'Bu görsel gösterilemiyor.' });
    ({ blob } = await app.api.fetchFile(info.url, 'gorsel'));
  }
  return URL.createObjectURL(blob);
}

/** Çekmece görselleri: yüklenen URL'ler kapanışta bırakılır (geç gelen de). */
export function imageBag(app, studentId) {
  const urls = [];
  let closed = false;
  return {
    figure(file, caption) {
      const figure = h('figure', 'ak-sl-figure');
      const frame = h('div', 'ak-sl-figure-img');
      frame.append(h('span', 'ak-muted', 'Görsel yükleniyor…'));
      figure.append(frame, h('figcaption', '', caption));
      imageUrl(app, studentId, file).then((url) => {
        if (!url) { frame.replaceChildren(h('span', 'ak-muted', 'Görsel yok.')); return; }
        if (closed) { URL.revokeObjectURL(url); return; }
        urls.push(url);
        const image = h('img');
        image.alt = caption;
        image.src = url;
        frame.replaceChildren(image);
      }).catch((failure) => {
        frame.replaceChildren(h('span', 'ak-muted', problem(failure, 'Görsel yüklenemedi.')));
      });
      return figure;
    },
    close() {
      closed = true;
      for (const url of urls.splice(0)) URL.revokeObjectURL(url);
    },
  };
}

// --------------------------------------------------------------- soru takibi

/** Dönemin (hafta/ay/yıl) gün aralığı — konu tablosu aynı aralıkla süzülür. */
export function periodRange(period, date) {
  if (period === 'week') {
    const from = weekStart(date);
    return { from, to: addDays(from, 6) };
  }
  const [year, month] = date.split('-').map(Number);
  if (period === 'year') return { from: `${year}-01-01`, to: `${year}-12-31` };
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const mm = String(month).padStart(2, '0');
  return { from: `${year}-${mm}-01`, to: `${year}-${mm}-${String(last).padStart(2, '0')}` };
}

export function questionsTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const state = { period: 'week', date: todayIst(), year: Number(todayIst().slice(0, 4)), sort: 'weakest', minQuestions: 10 };
  const range = h('span', 'ak-muted');
  const periodSelect = selectBox([{ value: 'week', label: 'Haftalık' }, { value: 'month', label: 'Aylık' },
    { value: 'year', label: 'Yıllık' }], state.period, (value) => { state.period = value; reloadPeriod(); }, { label: 'Dönem' });
  const dayField = dateInput(state.date, (iso) => {
    if (!iso) return;
    state.date = iso;
    reloadPeriod();
  }, 'Dönemin günü');
  node.append(toolbar(periodSelect.node, dayField.node, range));

  const stats = loader(() => app.api.get(`/v1/students/${studentId}/question-stats`, { period: state.period, date: state.date }),
    (data) => renderStats(data), { rows: 5, columns: 6 });

  const yearLabel = h('b', '', String(state.year));
  const heat = loader(() => app.api.get(`/v1/students/${studentId}/question-stats/heatmap`, { year: state.year }),
    (data) => renderHeat(data), { rows: 6, columns: 8 });
  const stepYear = (delta) => { state.year += delta; yearLabel.textContent = String(state.year); heat.reload(); };

  const sortSelect = selectBox([{ value: 'weakest', label: 'En zayıf konular' }, { value: 'most_solved', label: 'En çok çözülen' },
    { value: 'recent', label: 'Son çalışılan' }], state.sort, (value) => { state.sort = value; topics.reload(); }, { label: 'Sıralama' });
  const minSelect = selectBox([5, 10, 20, 50].map((value) => ({ value, label: `En az ${value} soru` })), state.minQuestions,
    (value) => { state.minQuestions = Number(value); topics.reload(); }, { label: 'En az soru' });
  const topics = pagedList({
    app,
    path: `/v1/students/${studentId}/question-stats/topics`,
    query: () => ({ sort: state.sort, minQuestions: state.minQuestions, ...periodRange(state.period, state.date) }),
    limit: 20,
    dense: true,
    rowKey: (row) => row.topic?.id,
    columns: [
      { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => stack(row.topic?.name, row.subject?.name) },
      { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questions) },
      { key: 'dyb', label: 'D / Y / B', width: '110px', cell: (row) => `${num(row.correct)} / ${num(row.wrong)} / ${num(row.blank)}` },
      { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => badge(percent(row.accuracyPercent, 0), accuracyTone(row.accuracyPercent)) },
      { key: 'last', label: 'Son çözüm', width: '130px', cell: (row) => fmtDateTime(row.lastSolvedAt) },
      { key: 'mistakes', label: 'Hata', align: 'num', cell: (row) => num(row.mistakeCount) },
      { key: 'flags', label: '', width: '170px', cell: (row) => {
        const cell = h('div', 'ak-inline');
        if (row.inReviewQueue) cell.append(badge('Tekrar listesinde', 'info'));
        if (row.plannedTaskId) cell.append(badge('Planda', 'good'));
        return cell;
      } },
    ],
    empty: { title: 'Bu dönemde yeterli soru çözülmemiş', text: '“En az soru” eşiğini düşürün ya da dönemi genişletin.' },
  });

  const comparison = loader(async () => {
    try {
      return await app.api.get(`/v1/students/${studentId}/question-stats/class-comparison`,
        { period: state.period === 'week' ? 'week' : 'month', date: state.date });
    } catch (failure) {
      if (isFeatureOff(failure)) return { featureOff: true };
      throw failure;
    }
  }, (data) => renderComparison(data), { rows: 3, columns: 4 });

  const heatTools = toolbar(button('‹', { variant: 'ghost', title: 'Önceki yıl', onClick: () => stepYear(-1) }), yearLabel,
    button('›', { variant: 'ghost', title: 'Sonraki yıl', onClick: () => stepYear(1) }));
  node.append(stats.node,
    card('Yıllık soru takvimi', box(heatTools, heat.node)),
    card('Konu bazlı doğruluk', box(toolbar(sortSelect.node, minSelect.node), topics.node)));
  // Anonim sınıf kıyası kurum bayrağıdır; kapalıysa kart hiç çizilmez, istek gitmez.
  const showComparison = app.enabled('class_comparison');
  if (showComparison) node.append(card('Sınıf ortalamasıyla kıyas (anonim)', comparison.node));

  function reloadPeriod() {
    const year = Number(state.date.slice(0, 4));
    const tasks = [stats.reload(), topics.reload(), showComparison ? comparison.reload() : null];
    if (year !== state.year) { state.year = year; yearLabel.textContent = String(year); tasks.push(heat.reload()); }
    return Promise.all(tasks);
  }

  function renderStats(data) {
    range.textContent = `${fmtDate(data.from)} – ${fmtDate(data.to)} · özet ${fmtDateTime(data.computedAt)}`;
    const totals = data.totals || {};
    const previous = data.previous || {};
    const today = data.today || {};
    const delta = previous.questions
      ? { percent: Math.round(((totals.questions - previous.questions) / previous.questions) * 100),
        title: `Önceki dönem: ${num(previous.questions)} soru` } : null;
    const nodes = [kpiRow([
      { label: 'Çözülen soru', value: num(totals.questions), delta },
      { label: 'Doğruluk', value: percent(totals.accuracyPercent, 0),
        title: previous.accuracyPercent !== null && previous.accuracyPercent !== undefined
          ? `Önceki dönem ${percent(previous.accuracyPercent, 0)}` : '' },
      { label: 'Doğru / yanlış / boş', value: `${num(totals.correct)} / ${num(totals.wrong)} / ${num(totals.blank)}` },
      { label: 'Soru başı süre', value: totals.avgSecondsPerQuestion === null || totals.avgSecondsPerQuestion === undefined
        ? '—' : `${num(totals.avgSecondsPerQuestion, 0)} sn` },
      { label: 'Aktif gün', value: num(totals.activeDays) },
      { label: 'Bugün', value: today.target ? `${num(today.questions)} / ${num(today.target)}` : num(today.questions),
        tone: today.goalMet ? 'good' : '', title: today.goalMet ? 'Günlük soru hedefi tamamlandı.' : 'Günlük soru hedefi' },
    ])];
    const subjects = dataTable({
      columns: [
        { key: 'subject', label: 'Ders', width: 'minmax(0, 1.6fr)', cell: (row) => row.subject?.name || '—' },
        { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questions) },
        { key: 'dyb', label: 'D / Y / B', width: '110px', cell: (row) => `${num(row.correct)} / ${num(row.wrong)} / ${num(row.blank)}` },
        { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => badge(percent(row.accuracyPercent, 0), accuracyTone(row.accuracyPercent)) },
        { key: 'seconds', label: 'sn/soru', align: 'num', cell: (row) => (row.avgSecondsPerQuestion === null
          || row.avgSecondsPerQuestion === undefined ? '—' : num(row.avgSecondsPerQuestion, 0)) },
      ],
      rows: data.bySubject || [],
      rowKey: (row) => row.subject?.id,
      dense: true,
      empty: empty('Bu dönemde soru kaydı yok'),
    });
    const resources = barChart((data.byResource || []).map((row) => ({ label: row.resource?.title || '—',
      value: row.questions, display: `${num(row.questions)} · ${percent(row.accuracyPercent, 0)}` })), { max: 8 });
    const grid = h('div', 'ak-grid-2');
    grid.append(card('Ders kırılımı', subjects.node), card('Kaynak kırılımı', resources));
    nodes.push(grid);
    const trend = (data.accuracyTrend || []).filter((row) => row.accuracyPercent !== null && row.accuracyPercent !== undefined)
      .map((row) => ({ label: dayMonth(row.periodStart), value: row.accuracyPercent }));
    const records = data.records || {};
    const lower = h('div', 'ak-grid-2');
    lower.append(
      card('Doğruluk trendi', trend.length > 1 ? lineChart(trend, { height: 170, valueFormat: (value) => `%${Math.round(value)}` })
        : h('div', 'ak-muted ak-card-pad', 'Trend için en az iki dönem verisi gerekiyor.')),
      card('Rekorlar', kv([
        ['En iyi gün', records.bestDay?.date ? `${fmtDate(records.bestDay.date)} · ${num(records.bestDay.questions)} soru` : '—'],
        ['En iyi hafta', records.bestWeek?.weekStart ? `${fmtDate(records.bestWeek.weekStart)} haftası · `
          + `${num(records.bestWeek.questions)} soru` : '—'],
        ['En iyi ay', records.bestMonth?.month ? `${monthLong(records.bestMonth.month)} · ${num(records.bestMonth.questions)} soru` : '—'],
      ])),
    );
    nodes.push(lower);
    return nodes;
  }

  function renderHeat(data) {
    const year = data.year || state.year;
    const byDate = new Map((data.days || []).map((day) => [day.date, day]));
    const grid = h('div', 'ak-sl-heat');
    grid.append(h('span', 'ak-sl-heat-label', ''));
    for (let day = 1; day <= 31; day += 1) grid.append(h('span', 'ak-sl-heat-label', String(day)));
    for (let month = 0; month < 12; month += 1) {
      grid.append(h('span', 'ak-sl-heat-label', MONTHS_SHORT[month]));
      const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
      for (let day = 1; day <= 31; day += 1) {
        if (day > last) { grid.append(h('span', 'ak-sl-heat-cell off')); continue; }
        const iso = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const entry = byDate.get(iso);
        const count = entry?.questions || 0;
        const cell = h('span', `ak-sl-heat-cell l${entry?.level || 0}`, count ? String(count) : '');
        cell.title = `${fmtDate(iso)} · ${num(count)} soru`;
        grid.append(cell);
      }
    }
    const legend = h('div', 'ak-sl-heat-legend');
    legend.append(h('span', 'ak-muted', 'Az'));
    for (let level = 0; level <= 4; level += 1) legend.append(h('span', `ak-sl-heat-cell l${level}`));
    legend.append(h('span', 'ak-muted', 'Çok'));
    const summaryLine = h('div', 'ak-muted ak-sl-note', `${data.subject ? `${data.subject.name} · ` : ''}${num(data.totalQuestions)} soru · `
      + `${num(data.activeDays)} aktif gün · en yoğun gün ${num(data.maxQuestions)} soru · özet ${fmtDateTime(data.computedAt)}`);
    const scroller = h('div', 'ak-sl-heat-wrap');
    scroller.append(grid);
    return [summaryLine, scroller, legend];
  }

  function renderComparison(data) {
    if (data.featureOff) {
      return alertBox('Sınıf kıyası kurumda kapalı. Açıldığında öğrencinin dönemlik soru '
        + 'sayısı ve doğruluğu sınıf ve kurum ortalamasıyla anonim olarak kıyaslanır.', 'info');
    }
    if (data.hiddenReason === 'too_few_participants') {
      return alertBox(`Bu dönemde kıyas için yeterli katılımcı yok (${num(data.participantCount)} öğrenci). `
        + 'Kimlik sızmasın diye ortalama gösterilmez.', 'info');
    }
    const rows = [
      { key: 'me', label: 'Bu öğrenci', ...data.me },
      { key: 'class', label: 'Sınıf ortalaması', ...data.classAverage },
      { key: 'institution', label: 'Kurum ortalaması', ...data.institutionAverage },
    ];
    const table = dataTable({
      columns: [
        { key: 'label', label: '', width: 'minmax(0, 1.4fr)' },
        { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questions, row.key === 'me' ? 0 : 1) },
        { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => percent(row.accuracyPercent, 0) },
        { key: 'minutes', label: 'Çalışma', align: 'num', cell: (row) => `${num(row.minutes, row.key === 'me' ? 0 : 1)} dk` },
      ],
      rows,
      rowKey: (row) => row.key,
      dense: true,
    });
    const head = h('div', 'ak-inline ak-card-pad');
    head.append(data.band ? statusBadge(BANDS, data.band) : badge('Konum bandı yok', 'dim'),
      h('span', 'ak-muted', `${fmtDate(data.from)} – ${fmtDate(data.to)} · ${num(data.participantCount)} katılımcı · `
        + 'sıra numarası verilmez, konum bant olarak gösterilir'));
    return [head, table.node];
  }

  return {
    node,
    reload: () => Promise.all([stats.reload(), heat.reload(), topics.reload(),
      showComparison ? comparison.reload() : null]),
    destroy() { topics.destroy(); dayField.destroy(); },
  };
}

// --------------------------------------------------------------------- okuma

function shelfColumns() {
  return [
    { key: 'book', label: 'Kitap', width: 'minmax(0, 2fr)', cell: (row) => stack(row.book?.title, row.book?.author) },
    { key: 'status', label: 'Durum', width: '130px', cell: (row) => statusBadge(SHELF_STATUS, row.status) },
    { key: 'progress', label: 'İlerleme', width: '140px', cell: (row) => (row.book?.pageCount
      ? `${num(row.currentPage)}/${num(row.book.pageCount)} s${row.progressPercent !== null && row.progressPercent !== undefined
        ? ` · ${percent(row.progressPercent, 0)}` : ''}` : `${num(row.pagesRead)} s`) },
    { key: 'time', label: 'Süre', width: '140px', cell: (row) => `${minutes(row.minutesTotal)} · ${num(row.sessionCount)} oturum` },
    { key: 'last', label: 'Son okuma', width: '130px', cell: (row) => fmtDateTime(row.lastReadAt) },
    { key: 'source', label: 'Kaynak', width: 'minmax(0, 1fr)', cell: (row) => row.readingList?.title || SHELF_SOURCE[row.source] || '—' },
    { key: 'rating', label: 'Puan', width: '90px', cell: (row) => (row.review ? `${'★'.repeat(row.review.rating)} ${row.review.rating}/5` : '—') },
  ];
}

function sessionColumns({ withBook = true } = {}) {
  return [
    { key: 'at', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.startedAt) },
    withBook ? { key: 'book', label: 'Kitap', width: 'minmax(0, 1.8fr)', cell: (row) => row.bookTitle || '—' } : null,
    { key: 'minutes', label: 'Süre', align: 'num', cell: (row) => minutes(row.minutes) },
    { key: 'pages', label: 'Sayfa', width: '130px', cell: (row) => (row.startPage && row.endPage
      ? `${num(row.startPage)}–${num(row.endPage)} (${num(row.pages)} s)` : `${num(row.pages)} s`) },
    { key: 'source', label: 'Kayıt', width: '110px', cell: (row) => SESSION_SOURCE[row.source] || row.source },
  ].filter(Boolean);
}

export function readingTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const year = Number(todayIst().slice(0, 4));
  const stats = loader(() => app.api.get(`/v1/students/${studentId}/reading/stats`, { year }),
    (data) => renderStats(data), { rows: 4, columns: 5 });

  let shelfStatus = null;
  const chips = chipRow(Object.entries(SHELF_STATUS).map(([key, [label]]) => ({ key, label })), null,
    (key) => { shelfStatus = key; shelf.reload(); });
  const shelf = pagedList({
    app,
    path: `/v1/students/${studentId}/reading/shelf`,
    query: () => ({ status: shelfStatus || undefined }),
    limit: 20,
    columns: shelfColumns(),
    onRow: (row) => shelfDrawer(app, studentId, row.id),
    empty: { title: 'Rafta kitap yok', text: 'Öğrenci uygulamadan kurum listesinden, katalogdan ya da elle kitap ekler.' },
  });
  const sessions = pagedList({
    app,
    path: `/v1/students/${studentId}/reading/sessions`,
    limit: 20,
    dense: true,
    columns: sessionColumns(),
    empty: { title: 'Okuma oturumu yok' },
  });

  let scope = 'class_group';
  const scopeSelect = selectBox([{ value: 'class_group', label: 'Sınıf ligi' }, { value: 'institution', label: 'Kurum ligi' }],
    scope, (value) => { scope = value; league.reload(); }, { label: 'Lig kapsamı' });
  const league = loader(async () => {
    try {
      return await app.api.get(`/v1/students/${studentId}/reading/league`, { scope });
    } catch (failure) {
      if (isFeatureOff(failure)) return { featureOff: true };
      throw failure;
    }
  }, (data) => renderLeague(data), { rows: 5, columns: 3 });

  const lower = h('div', 'ak-grid-2');
  // Okuma ligi kurum bayrağıdır; kapalıysa kart hiç çizilmez, istek gitmez.
  const showLeague = app.enabled('reading_league');
  lower.append(card('Okuma oturumları', sessions.node));
  if (showLeague) lower.append(card('Okuma ligi', box(toolbar(scopeSelect.node), league.node)));
  node.append(stats.node, card('Kitap rafı', box(chips.node, shelf.node)), lower);

  function renderStats(data) {
    const today = data.today || {};
    const goal = today.goal?.target
      ? `Hedef ${num(today.goal.target)} ${today.goal.unit === 'minutes' ? 'dakika' : 'sayfa'}${today.goalMet ? ' · tamamlandı' : ''}`
      : 'Günlük hedef tanımlı değil';
    const nodes = [kpiRow([
      { label: 'Bugün', value: `${num(today.pages)} s · ${minutes(today.minutes)}`, tone: today.goalMet ? 'good' : '', title: goal },
      { label: 'Okuma serisi', value: `${num(data.streak?.current)} gün`, title: `En uzun seri ${num(data.streak?.best)} gün` },
      { label: 'Okuma hızı', value: data.pagesPerMinute === null || data.pagesPerMinute === undefined
        ? '—' : `${num(data.pagesPerMinute, 2)} sayfa/dk`, title: 'Son 30 gün' },
      { label: 'Bu ay', value: `${num(data.month?.pages)} s · ${num(data.month?.booksFinished)} kitap` },
      { label: `${year} yılı`, value: `${num(data.year?.pages)} s · ${num(data.year?.booksFinished)} kitap` },
    ])];
    nodes.push(h('div', 'ak-muted ak-sl-note', `${goal} · özet ${fmtDateTime(data.computedAt)}`));
    const monthly = (data.monthly || []).filter((row) => row.pages || row.booksFinished || row.minutes);
    if (monthly.length) {
      nodes.push(card('Aylık okuma', barChart(monthly.map((row) => ({ label: monthLong(row.month), value: row.pages,
        display: `${num(row.pages)} s · ${num(row.booksFinished)} kitap` })), { max: 12 })));
    }
    return nodes;
  }

  function renderLeague(data) {
    if (data.featureOff) return alertBox('Okuma ligi kurumda kapalı.', 'info');
    const table = dataTable({
      columns: [
        { key: 'rank', label: 'Sıra', align: 'num', width: '60px', cell: (row) => num(row.rank) },
        { key: 'name', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => {
          const cell = h('div', 'ak-inline');
          cell.append(h(row.isMe ? 'b' : 'span', '', row.displayName || '—'));
          if (row.isMe) cell.append(badge('Bu öğrenci', 'info'));
          return cell;
        } },
        { key: 'pages', label: 'Sayfa', align: 'num', cell: (row) => num(row.pages) },
      ],
      rows: data.standings || [],
      rowKey: (row) => `${row.rank}-${row.displayName}`,
      dense: true,
      empty: empty('Sıralama yok', 'Bu dönemde lige katılan yok.'),
    });
    return [kv([
      ['Dönem', data.period ? `${data.period.name} · ${fmtDate(data.period.from)} – ${fmtDate(data.period.to)}` : '—'],
      ['Katılımcı', num(data.participantCount)],
      ['Bu öğrenci', data.me?.rank ? `${num(data.me.rank)}. sıra · ${num(data.me.pages)} sayfa` : `Sıralamada değil · ${num(data.me?.pages)} sayfa`],
    ]), table.node];
  }

  return {
    node,
    reload: () => Promise.all([stats.reload(), shelf.reload(), sessions.reload(),
      showLeague ? league.reload() : null]),
    destroy() { shelf.destroy(); sessions.destroy(); },
  };
}

function shelfDrawer(app, studentId, readingItemId) {
  const images = imageBag(app, studentId);
  const sessions = pagedList({
    app,
    path: `/v1/students/${studentId}/reading/sessions`,
    query: { readingItemId },
    limit: 20,
    dense: true,
    columns: sessionColumns({ withBook: false }),
    empty: { title: 'Bu kitap için okuma oturumu yok' },
  });
  const panel = openDrawer(app, { title: 'Raftaki kitap', subtitle: 'Okuma rafı · salt okunur',
    onClose: () => { images.close(); sessions.destroy(); } });
  panel.node.classList.add('ak-sl-drawer');
  const view = loader(() => app.api.get(`/v1/students/${studentId}/reading/shelf/${readingItemId}`), (item) => {
    const book = item.book || {};
    panel.setTitle(book.title || 'Raftaki kitap');
    const head = h('div', 'ak-sl-book');
    if (book.cover?.id) head.append(images.figure(book.cover, 'Kapak'));
    head.append(kv([
      ['Kitap', [book.title, book.author].filter(Boolean).join(' · ')],
      ['Yayınevi / ISBN', [book.publisher, book.isbn].filter(Boolean).join(' · ') || '—'],
      ['Durum', statusBadge(SHELF_STATUS, item.status)],
      ['Kaynak', item.readingList?.title ? `${SHELF_SOURCE.institution_list}: ${item.readingList.title}` : SHELF_SOURCE[item.source] || '—'],
      ['İlerleme', book.pageCount ? `${num(item.currentPage)} / ${num(book.pageCount)} sayfa${item.progressPercent !== null
        && item.progressPercent !== undefined ? ` · ${percent(item.progressPercent, 0)}` : ''}` : `${num(item.pagesRead)} sayfa`],
      ['Toplam süre', `${minutes(item.minutesTotal)} · ${num(item.sessionCount)} oturum`],
      ['Başladı', fmtDateTime(item.startedAt)],
      item.finishedAt ? ['Bitti', fmtDateTime(item.finishedAt)] : null,
      item.abandonedAt ? ['Yarım bırakıldı', fmtDateTime(item.abandonedAt)] : null,
      ['Son okuma', fmtDateTime(item.lastReadAt)],
    ]));
    const nodes = [card('', head)];
    if (item.review) {
      nodes.push(card('Değerlendirme', kv([
        ['Puan', `${'★'.repeat(item.review.rating)} ${item.review.rating}/5`],
        ['Kısaca', item.review.summary || '—'],
        ['Yazıldı', fmtDateTime(item.review.updatedAt || item.review.createdAt)],
      ])));
    }
    nodes.push(card('Okuma oturumları', sessions.node));
    return nodes;
  }, { rows: 6, columns: 2 });
  panel.body.append(view.node);
  view.reload();
  sessions.reload();
}
