// BBD Akademi — Öğrenci detayı (F-A2): kayıt sekmeleri.
//
// Ödevler, Denemeler, Yoklama, Çalışma, Talepler, Kurum cari ve Kantin. Çekmece
// kabuğu `students.js` → `openStudent`, paylaşılan yardımcılar
// `students-detail.js` içindedir. Kurallar orada yazılı olanlarla aynıdır:
// sekme ilk açılınca yüklenir, bağımsız okumalar aynı anda gider, satır başına
// istek atılmaz, okumalar salt-okunur öğrenci uçlarıdır.
//
// İKİ PARA AYRI (A12). "Kurum cari" sekmesi mavi çizgilidir (`ak-money-finance`),
// "Kantin" turuncu (`ak-money-canteen`); hiçbir tutar öbürüyle toplanmaz ya da
// aynı kartta gösterilmez. Kantin SALT-OKUNURDUR: kantin kontrolleri ve kantin
// nakit tahsilatı KM'nin Öğrenciler modülündedir (`admin_patchStudentCanteenControls`
// ve `admin_collectCanteenCash` bu panelde bilerek yok).
//
// KARNE. `admin_exportReportCard` 202 dışa aktarım işi döndürür; ortak
// `exportJob` yoklar ve dosya hazır olunca indirir. Pencerenin Idempotency-Key'i
// işe de taşınır (çift tıklama ikinci iş açmaz).

import { button, foldText, h, monthName } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../../ui-kit/layout.js';
import { dateRange } from '../../../ui-kit/datefield.js';
import { barChart, groupedBar, lineChart } from '../../../ui-kit/charts.js';
import { timeline } from '../../../ui-kit/flow.js';
import { AkademiError, openBlob } from '../api.js';
import { BOOK_STATUS } from '../finance/books.js';
import { METHODS } from '../finance/finance.js';
import {
  DAY_LABEL, actionButton, empty, exportJob, fmtDate, fmtDateTime, fmtTime, formDialog, kv, loader,
  money, net, num, optionsOf, pagedList, percent, personName, problem, runAction, selectBox,
  statusBadge, todayIst, toolbar,
} from '../common.js';
import { documentButtons } from '../documents.js';
import { contractEntry } from '../contracts/wizard-entry.js';
import { attendanceExtras } from './students-attendance.js';
import { leaveKindDetail } from '../attendance/attendance-manage.js';
import { examResultDoc } from '../documents-logic.js';
import { analysisPdfButton, examAnalysisSection, reportCardButton } from '../exams/exams-analysis.js';
import {
  ATTENDANCE, EXAM_TYPES, HOMEWORK_STATUS, checkbox, dayOf, dqb, grid2, isSet, linkButton,
  kmManagedHint, padded, panelCard, secondsText, shortDate, stack, subDrawer, subjectLabel, table,
  topicResources,
} from './students-detail.js';

// ------------------------------------------------------------- etiketler

const EXAM_STATUS = { upcoming: ['Yaklaşan', 'info'], awaiting_results: ['Sonuç bekleniyor', 'warn'],
  published: ['Sonuçlandı', 'good'], absent: ['Girmedi', 'dim'] };
const TREND = { up: ['▲ Yükseliyor', 'good'], down: ['▼ Düşüyor', 'bad'], flat: ['— Sabit', 'dim'] };
const LOG_SOURCE = { timer: 'Zamanlayıcı', manual: 'Elle giriş', homework: 'Ödev', review: 'Tekrar',
  resource: 'Kaynak', quick_entry: 'Hızlı giriş', plan_task: 'Plan görevi' };
const LOG_MODE = { pomodoro: 'Pomodoro', free: 'Serbest', manual: 'Elle' };
const BLOCK_BY = { guardian: 'veli', institution: 'kurum' };
const REQ_STATUS = { requested: ['Bekliyor', 'warn'], approved: ['Onaylandı', 'good'],
  scheduled: ['Zaman verildi', 'good'], declined: ['Reddedildi', 'dim'], cancelled: ['İptal', 'dim'],
  done: ['Tamamlandı', 'good'] };
const LEAVE_REASONS = { illness: 'Hastalık', family: 'Aile', travel: 'Seyahat', official: 'Resmî', other: 'Diğer' };
const MEETING_MODES = { in_person: 'Yüz yüze', phone: 'Telefon', online: 'Çevrim içi' };
const BOOK_PAYMENT = { card: 'Kartla ödendi', cari: 'Kurum carisine eklendi' };
const CHARGE_KINDS = { installment: 'Taksit', down_payment: 'Peşinat', book: 'Kitap', material: 'Materyal',
  other: 'Diğer' };
const CHARGE_STATUS = { open: ['Açık', 'info'], partially_paid: ['Kısmi ödendi', 'warn'], paid: ['Ödendi', 'good'],
  overdue: ['Gecikmiş', 'bad'], cancelled: ['İptal', 'dim'] };
const PAYMENT_STATUS = { completed: ['Tamamlandı', 'good'], voided: ['İptal edildi', 'dim'] };
const STATEMENT_KIND = { charge: 'Borç', charge_cancelled: 'Borç iptali', payment: 'Tahsilat',
  payment_voided: 'Tahsilat iptali', credit_applied: 'Alacak mahsubu', discount: 'İndirim / burs', refund: 'İade',
  refund_voided: 'İade iptali', withdrawal: 'Ayrılma / dondurma' };
const CANTEEN_METHOD = { cash: 'Nakit', card: 'Kart' };
const PR_STATUS = { pending: ['Bekliyor', 'warn'], paid: ['Ödendi', 'good'], expired: ['Süresi doldu', 'dim'],
  failed: ['Başarısız', 'bad'] };
const PR_ORIGIN = { admin: 'Yönetici', bulk: 'Toplu', threshold: 'Eşik', api: 'Kantin sistemi', app: 'Uygulama' };
const CANTEEN_HIDDEN = { balance: 'bakiye', transactions: 'harcamalar', limit: 'limit' };

const rank = (value) => (value ? `${num(value.rank)} / ${num(value.of)}` : '—');

function defaultExamType(student) {
  if (student?.examTarget === 'LGS') return 'lgs';
  if (student?.examTarget === 'YKS') return 'tyt';
  const level = Number(student?.gradeLevel);
  return level && level <= 8 ? 'lgs' : 'tyt';
}

function slug(text) {
  return foldText(text).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'ogrenci';
}

// ---------------------------------------------------------------- ödevler

export function homeworkTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let status = '';
  const statusSelect = selectBox([{ value: '', label: 'Tüm ödevler' }, { value: 'pending', label: 'Bekleyen' },
    { value: 'overdue', label: 'Geciken' }, { value: 'completed', label: 'Teslim edilen' }], '', (value) => {
    status = value; list.reload();
  }, { label: 'Durum' });
  const list = pagedList({
    app,
    path: `/v1/students/${id}/homework`,
    query: () => ({ status: status || undefined }),
    columns: [
      { key: 'title', label: 'Ödev', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, row.subject?.name) },
      { key: 'resource', label: 'Kaynak', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.resource?.title,
        [(row.sections || []).map((section) => section.title).join(', '),
          row.pageFrom ? `s. ${row.pageFrom}–${row.pageTo || row.pageFrom}` : ''].filter(Boolean).join(' · ')) },
      { key: 'assigned', label: 'Verildi', cell: (row) => stack(dayOf(row.assignedAt), personName(row.assignedBy)) },
      { key: 'due', label: 'Son gün', width: '130px', cell: (row) => fmtDateTime(row.dueAt) },
      { key: 'status', label: 'Durum', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(statusBadge(HOMEWORK_STATUS, row.status));
        if (row.completion?.late) cell.append(badge('Geç teslim', 'warn'));
        return cell;
      } },
      { key: 'result', label: 'Sonuç', cell: (row) => (row.completion ? dqb(row.completion) : '—') },
    ],
    onRow: (row) => homeworkDetail(ctx, row.id),
    empty: { title: 'Bu durumda ödev yok' },
  });
  ctx.dispose(() => list.destroy());
  node.append(toolbar(statusSelect.node, h('span', 'kit-spacer'),
    button('Ödev ver', { variant: 'ghost', onClick: () => app.go('homework', { compose: true, studentId: id,
      studentName: ctx.student?.student?.displayName || '' }) })), list.node);
  list.reload();
  return { node };
}

function homeworkDetail(ctx, homeworkId) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: 'Ödev' });
  const view = loader(() => app.api.get(`/v1/students/${id}/homework/${homeworkId}`), (homework) => {
    panel.setTitle(homework.title || 'Ödev');
    const nodes = [card('', kv([
      ['Ders', homework.subject?.name],
      ['Açıklama', homework.description],
      ['Kaynak', homework.resource?.title],
      ['Bölümler', (homework.sections || []).map((section) => `${section.title}${isSet(section.questionCount)
        ? ` (${num(section.questionCount)} soru)` : ''}`).join(', ') || '—'],
      ['Sayfa', homework.pageFrom ? `${homework.pageFrom}–${homework.pageTo || homework.pageFrom}` : '—'],
      ['Konular', (homework.topics || []).map((topic) => topic.name).join(', ') || '—'],
      ['Veren', `${personName(homework.assignedBy)} · ${fmtDateTime(homework.assignedAt)}`],
      ['Son gün', fmtDateTime(homework.dueAt)],
      ['Durum', statusBadge(HOMEWORK_STATUS, homework.status)],
    ]))];
    const completion = homework.completion;
    if (completion) {
      const images = h('div', 'ak-inline');
      (completion.images || []).forEach((image, index) => {
        if (image.hidden || !image.url) {
          images.append(badge(`Görsel ${index + 1} gizli`, 'dim'));
          return;
        }
        const ext = String(image.contentType || 'image/jpeg').split('/').pop();
        images.append(button(`Görsel ${index + 1}`, { variant: 'ghost', onClick: () => runAction(app, async () => {
          openBlob(await app.api.fetchFile(image.url, `odev-${index + 1}.${ext}`));
        }) }));
      });
      nodes.push(card('Teslim', kv([
        ['Teslim', `${fmtDateTime(completion.completedAt)}${completion.late ? ' · geç teslim' : ''}`],
        ['Sonuç', dqb(completion)],
        ['Not', completion.note],
        ['Görseller', images.childElementCount ? images : '—'],
      ])));
    } else {
      nodes.push(h('div', 'ak-muted', 'Henüz teslim edilmedi.'));
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

// -------------------------------------------------------------- denemeler

export function examsTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let type = '';
  let subject = '';
  const typeSelect = selectBox(optionsOf(EXAM_TYPES), 'tyt', (value) => {
    type = value;
    subject = '';
    subjectSelect.set('');
    reloadTyped();
  }, { label: 'Sınav türü' });
  const subjectSelect = selectBox([{ value: '', label: 'Toplam net' }], '', (value) => {
    subject = value; trend.reload();
  }, { label: 'Trend dersi' });
  app.lookups.options('subjects', { empty: 'Toplam net' }).then((options) => subjectSelect.options(options))
    .catch(() => {});
  const reportCard = actionButton(app, 'admin_exportReportCard', 'Karne (PDF/XLSX)', {
    onClick: () => exportReportCard(ctx, type || 'tyt') });
  node.append(toolbar(h('span', 'ak-muted', 'Sınav türü'), typeSelect.node, h('span', 'kit-spacer'), reportCard));

  const analytics = loader(() => app.api.get(`/v1/students/${id}/exam-analytics`, { type }), renderAnalytics,
    { rows: 4, columns: 6 });
  const trend = loader(() => app.api.get(`/v1/students/${id}/exam-trend`, { type, subject: subject || undefined }),
    renderTrend, { rows: 4, columns: 4 });
  const weak = loader(() => app.api.get(`/v1/students/${id}/weak-topics`, { type, limit: 20 }),
    (data) => renderWeak(ctx, data?.items), { rows: 4, columns: 5 });
  const exams = pagedList({
    app,
    path: `/v1/students/${id}/exams`,
    query: () => ({ type }),
    limit: 20,
    columns: [
      { key: 'name', label: 'Deneme', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = stack(row.name, row.publisher);
        if (row.isHistorical) cell.append(badge('Geçmiş kayıt', 'dim'));
        return cell;
      } },
      { key: 'date', label: 'Tarih', cell: (row) => fmtDate(row.date) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(EXAM_STATUS, row.status) },
      { key: 'net', label: 'Net', align: 'num', cell: (row) => net(row.totalNet) },
      { key: 'score', label: 'Tahmini puan', align: 'num', cell: (row) => (isSet(row.estimatedScore)
        ? num(row.estimatedScore, 1) : '—') },
    ],
    onRow: (row) => examResult(ctx, row),
    empty: { title: 'Bu türde kurum denemesi yok' },
  });
  const external = pagedList({
    app,
    path: `/v1/students/${id}/external-exams`,
    limit: 20,
    columns: [
      { key: 'name', label: 'Deneme', width: 'minmax(0, 2fr)', cell: (row) => stack(row.name, row.publisher || row.label) },
      { key: 'type', label: 'Tür', cell: (row) => EXAM_TYPES[row.type] || row.type },
      { key: 'date', label: 'Tarih', cell: (row) => fmtDate(row.date) },
      { key: 'net', label: 'Net', align: 'num', cell: (row) => net(row.totalNet) },
      // Yönetici öğrenci adına girdiyse `actedBy` "Yönetici tarafından" (Akademi README §14 madde 65).
      { key: 'by', label: 'Giren', cell: (row) => stack(row.enteredBy?.displayName,
        row.actedBy || (row.enteredBy?.kind === 'guardian' ? 'Veli' : 'Öğrenci')) },
    ],
    onRow: (row) => externalExam(ctx, row, () => external.reload()),
    empty: { title: 'Dış deneme girilmemiş', text: 'Öğrenci ya da veli uygulamadan dışarıdaki deneme sonuçlarını girebilir.' },
  });
  ctx.dispose(() => { exams.destroy(); external.destroy(); });

  node.append(card('Analiz', analytics.node), panelCard('Net trendi', trend.node, [subjectSelect.node]),
    card('Eksik konular', weak.node, 'Konu bazlı hata analizi'), card('Kurum denemeleri', exams.node),
    card('Dışarıdaki denemeler', external.node, 'Öğrenci / veli girişi'));

  function reloadTyped() {
    analytics.reload();
    trend.reload();
    weak.reload();
    exams.reload();
  }
  // Varsayılan tür öğrencinin hedef sınavından (başlık yüklenince).
  ctx.ready.then((data) => {
    type = defaultExamType(data?.student);
    typeSelect.set(type);
    reloadTyped();
  });
  external.reload();
  return { node };
}

function renderAnalytics(data) {
  const distance = data.distanceToTargetNet;
  const nodes = [kpiRow([
    { label: 'Deneme', value: num(data.examCount) },
    { label: 'Ortalama net', value: net(data.averageNet) },
    { label: 'En iyi', value: net(data.bestNet) },
    { label: 'Son', value: net(data.lastNet) },
    { label: 'Hedef', value: net(data.targetNet) },
    { label: 'Hedefe uzaklık', value: net(distance), tone: Number(distance) > 0 ? 'warn' : '' },
  ])];
  const rows = data.subjectBreakdown || [];
  if (!rows.length) {
    nodes.push(empty('Bu türde sonuçlanmış deneme yok'));
    return nodes;
  }
  nodes.push(groupedBar(rows.map((row) => ({ label: row.subject?.shortName || row.subject?.name || '—',
    values: [Number(row.averageNet) || 0, Number(row.lastNet) || 0] })),
  { series: ['Ortalama net', 'Son net'], format: (value) => num(value, 1) }));
  nodes.push(table([
    { key: 'subject', label: 'Ders', width: 'minmax(0, 2fr)', cell: (row) => row.subject?.name || '—' },
    { key: 'average', label: 'Ortalama', align: 'num', cell: (row) => net(row.averageNet) },
    { key: 'last', label: 'Son', align: 'num', cell: (row) => net(row.lastNet) },
    { key: 'trend', label: 'Eğilim', cell: (row) => statusBadge(TREND, row.trend) },
  ], rows, 'Ders kırılımı yok'));
  return nodes;
}

function renderTrend(data) {
  const points = data.points || [];
  if (!points.length) return empty('Trend için deneme yok', 'Bu türde sonuçlanmış deneme olunca çizilir.');
  const external = points.some((point) => point.source === 'student_entry');
  return [lineChart(points.map((point) => ({ label: `${shortDate(point.date)}${point.source === 'student_entry' ? '*' : ''}`,
    value: Number(point.net) || 0 })), { valueFormat: (value) => num(value, 1) }),
  h('div', 'kit-card-body ak-muted', [`${data.subject?.name || 'Toplam'} neti`,
    isSet(data.targetNet) ? `hedef ${net(data.targetNet)}` : '',
    external ? '* öğrencinin / velinin girdiği dış deneme' : ''].filter(Boolean).join(' · '))];
}

function renderWeak(ctx, items) {
  return table([
    { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => stack(row.topic?.name,
      subjectLabel(ctx, row.topic?.subjectId)) },
    { key: 'wrong', label: 'Yanlış', align: 'num', cell: (row) => num(row.wrong) },
    { key: 'blank', label: 'Boş', align: 'num', cell: (row) => num(row.blank) },
    { key: 'count', label: 'Soru', align: 'num', cell: (row) => num(row.questionCount) },
    { key: 'rate', label: 'Hata oranı', align: 'num', cell: (row) => percent((Number(row.errorRate) || 0) * 100, 0) },
    { key: 'exams', label: 'Deneme', align: 'num', cell: (row) => num(row.examCount) },
    { key: 'queue', label: 'Tekrar', cell: (row) => (row.inReviewQueue ? badge('Kuyrukta', 'info') : '—') },
    { key: 'res', label: '', width: '90px', cell: (row) => linkButton('Kaynaklar', () => topicResources(ctx, row.topic.id)) },
  ], items, 'Eksik konu yok');
}

function examResult(ctx, row) {
  const { app, id } = ctx;
  if (row.status === 'upcoming') {
    app.toast('Bu deneme henüz yapılmadı.', 'warn');
    return;
  }
  const panel = subDrawer(ctx, { title: row.name || 'Deneme', subtitle: `${EXAM_TYPES[row.type] || ''} · ${fmtDate(row.date)}` });
  const view = loader(() => app.api.get(`/v1/students/${id}/exams/${row.examId}`), (result) => {
    // Karne (PDF): öğrencinin / velinin "Karnemi indir"iyle aynı belge (Akademi README §14 madde 138).
    const nodes = [toolbar(h('span', 'kit-spacer'), result.absent ? null
      : reportCardButton(app, { studentId: id, examId: row.examId }), result.absent ? null
      : analysisPdfButton(app, { studentId: id, examId: row.examId }), documentButtons(app, {
      title: 'Deneme sonuç belgesi', label: 'Sonuç belgesi', build: async () => examResultDoc(result,
        (await app.api.get(`/v1/admin/students/${id}`))?.student) }))];
    if (result.absent) nodes.push(alertBox('Öğrenci bu denemeye girmedi.', 'warn'));
    const score = result.estimatedScore || {};
    nodes.push(kpiRow([
      { label: 'Toplam net', value: net(result.totalNet) },
      { label: score.label || 'Puan', value: isSet(score.value) ? num(score.value, 2) : '—',
        title: score.isEstimate ? 'Tahmini puan' : '' },
      { label: 'Kurum sırası', value: rank(result.ranks?.institution) },
      { label: 'Sınıf sırası', value: rank(result.ranks?.classGroup) },
      { label: 'Yayınevi geneli', value: rank(result.ranks?.publisherNational) },
      { label: 'Hedefe uzaklık', value: net(result.distanceToTargetNet) },
    ]), card('', kv([
      ['Kitapçık', result.bookletType],
      ['Soru sayısı', num(result.exam?.questionTotal)],
      ['Sınıf ortalaması', net(result.classAverageNet)],
      ['Kurum ortalaması', net(result.institutionAverageNet)],
      ['Hedef net', net(result.targetNet)],
    ])), card('Ders kırılımı', table([
      { key: 'title', label: 'Ders / bölüm', width: 'minmax(0, 2fr)', cell: (item) => stack(item.title, item.subject?.name) },
      { key: 'q', label: 'Soru', align: 'num', cell: (item) => num(item.questionCount) },
      { key: 'c', label: 'D', align: 'num', cell: (item) => num(item.correct) },
      { key: 'w', label: 'Y', align: 'num', cell: (item) => num(item.wrong) },
      { key: 'b', label: 'B', align: 'num', cell: (item) => num(item.blank) },
      { key: 'net', label: 'Net', align: 'num', cell: (item) => net(item.net) },
      { key: 'class', label: 'Sınıf ort.', align: 'num', cell: (item) => net(item.classAverageNet) },
      { key: 'inst', label: 'Kurum ort.', align: 'num', cell: (item) => net(item.institutionAverageNet) },
    ], result.sections, 'Bölüm yok')), card('Bu denemedeki eksik konular', renderWeak(ctx, result.weakTopics)));
    // Tam ayrıntılı analiz: bütün sorular (önce yanlışlar, sonra boşlar) + bütün konular (madde 138)
    if (!result.absent) {
      const analysis = examAnalysisSection(app, { studentId: id, examId: row.examId });
      nodes.push(analysis.node);
      analysis.reload();
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

// Dış deneme ayrıntısı. Liste satırı (`students_listExternalExams`) çekmeceye
// hemen çizilir; açılışta ayrıntı ucu (`students_getExternalExam`) güncel kaydı
// getirip yerine koyar — öğrenci / veli kaydı bu arada düzeltmiş ya da silmiş
// olabilir. Silinmişse (404 `external_exam_not_found`) sebep yazılır ve liste
// tazelenir; başka bir hatada (ör. Akademi henüz bu ucu sunmuyor) liste
// satırındaki bilgi kalır, üstte not görünür.
function externalExam(ctx, row, onGone) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: row.name || 'Dış deneme',
    subtitle: `${EXAM_TYPES[row.type] || ''} · ${fmtDate(row.date)} · ${row.enteredBy?.displayName || ''}` });
  const body = h('div', 'ak-stack');
  body.replaceChildren(...externalExamBody(ctx, row));
  panel.body.append(body);
  app.api.get(`/v1/students/${id}/external-exams/${row.id}`).then((exam) => {
    body.replaceChildren(...externalExamBody(ctx, exam));
  }, (failure) => {
    if (failure instanceof AkademiError && failure.code === 'external_exam_not_found') {
      body.replaceChildren(alertBox('Bu dış deneme öğrenci ya da veli tarafından silinmiş.', 'warn'));
      onGone?.();
      return;
    }
    body.prepend(alertBox(`Güncel kayıt okunamadı (${problem(failure)}); listedeki bilgi gösteriliyor.`, 'warn'));
  });
}

function externalExamBody(ctx, exam) {
  return [kpiRow([{ label: 'Toplam net', value: net(exam.totalNet) },
    { label: 'Girildi', value: dayOf(exam.createdAt) }]), card('Ders kırılımı', table([
    { key: 'subject', label: 'Ders', width: 'minmax(0, 2fr)', cell: (item) => stack(subjectLabel(ctx, item.subjectId), item.title) },
    { key: 'q', label: 'Soru', align: 'num', cell: (item) => num(item.questionCount) },
    { key: 'result', label: 'Sonuç', cell: (item) => dqb(item) },
    { key: 'net', label: 'Net', align: 'num', cell: (item) => net(item.net) },
  ], exam.sections, 'Bölüm yok'))];
}

async function exportReportCard(ctx, type) {
  const { app, id } = ctx;
  const choice = await formDialog(app, {
    title: 'Öğrenci karnesi',
    description: 'Seçilen türdeki denemelerin sonuçları tek dosyada. Dosya hazırlanınca iner; PDF yazdırmak için açılır.',
    value: { examType: type, format: 'pdf', from: '', to: '' },
    fields: [
      { key: 'examType', label: 'Sınav türü', type: 'select', options: optionsOf(EXAM_TYPES) },
      { key: 'format', label: 'Biçim', type: 'select', options: [{ value: 'pdf', label: 'PDF (yazdırma)' },
        { value: 'xlsx', label: 'Excel (XLSX)' }] },
      { key: 'from', label: 'Başlangıç (isteğe bağlı)', type: 'date' },
      { key: 'to', label: 'Bitiş (isteğe bağlı)', type: 'date' },
    ],
    submitLabel: 'Hazırla',
    onSubmit: (draft, { key }) => {
      if (draft.from && draft.to && draft.to < draft.from) {
        throw new AkademiError({ message: 'Bitiş günü başlangıçtan önce olamaz.' });
      }
      return { ...draft, key };
    },
  });
  if (!choice) return;
  const name = `karne-${slug(ctx.student?.student?.displayName)}-${choice.examType}`;
  await exportJob(app, () => app.api.post(`/v1/admin/students/${id}/report-card`, {
    examType: choice.examType, from: choice.from || null, to: choice.to || null, format: choice.format,
  }, { key: choice.key }), { name, label: 'Karne hazırlanıyor…' });
}

// ---------------------------------------------------------------- yoklama

export function attendanceTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let month = todayIst().slice(0, 7);
  const label = h('b', 'ak-stu-period');
  const shift = (delta) => {
    if (delta) {
      const [year, value] = month.split('-').map(Number);
      month = new Date(Date.UTC(year, value - 1 + delta, 1)).toISOString().slice(0, 7);
    } else {
      month = todayIst().slice(0, 7);
    }
    paintLabel();
    view.reload();
    extras.calendar?.setMonth(month);
  };
  const extras = attendanceExtras(ctx, { onLeaveSaved: () => {
    view.reload();
    leaves.reload();
    extras.calendar?.reload();
  } });
  const paintLabel = () => {
    const [year, value] = month.split('-').map(Number);
    label.textContent = `${monthName(value - 1)} ${year}`;
  };
  node.append(toolbar(
    button('‹ Önceki ay', { variant: 'ghost', onClick: () => shift(-1) }), label,
    button('Sonraki ay ›', { variant: 'ghost', onClick: () => shift(1) }),
    button('Bu ay', { variant: 'ghost', onClick: () => shift(0) }),
    h('span', 'kit-spacer'),
    extras.leave,
    app.has('requests') ? button('İzin taleplerini yönet', { variant: 'ghost',
      onClick: () => app.go('requests', { tab: 'leave' }) }) : null,
  ));
  const view = loader(() => app.api.get(`/v1/students/${id}/attendance`, { month }), renderAttendance,
    { rows: 6, columns: 5 });
  const leaves = pagedList({
    app,
    path: `/v1/students/${id}/leave-requests`,
    limit: 20,
    columns: [
      { key: 'range', label: 'Tarih', width: '170px', cell: (row) => (row.from === row.to ? fmtDate(row.from)
        : `${fmtDate(row.from)} – ${fmtDate(row.to)}`) },
      // Tür (V4): tam gün / saat aralığı / erken çıkış / mazeret ayrı görünür.
      { key: 'kind', label: 'Tür', width: 'minmax(130px, 1fr)', cell: (row) => {
        const info = leaveKindDetail(row);
        const cell = h('div', 'ak-cell-stack');
        cell.append(badge(info.label, info.tone));
        if (info.detail) cell.append(h('small', '', info.detail));
        return cell;
      } },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)', cell: (row) => stack(LEAVE_REASONS[row.reason]
        || row.reason, row.description) },
      { key: 'by', label: 'Bildiren', cell: (row) => stack(personName(row.createdBy), fmtDateTime(row.createdAt)) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(REQ_STATUS, row.status) },
      { key: 'decision', label: 'Karar', cell: (row) => (row.decidedAt
        ? stack(personName(row.decidedBy), row.decisionNote) : '—') },
    ],
    empty: { title: 'İzin bildirimi yok' },
  });
  ctx.dispose(() => leaves.destroy());
  node.append(...extras.cards, card('Aylık yoklama', view.node), card('İzin (mazeret) bildirimleri', leaves.node));
  if (extras.note) node.append(extras.note);
  paintLabel();
  view.reload();
  extras.calendar?.reload();
  leaves.reload();
  return { node };
}

function renderAttendance(data) {
  const summary = data.summary || {};
  const items = [...(data.items || [])].sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt)));
  return [kpiRow([
    { label: 'Ders', value: num(summary.sessionCount) },
    { label: 'Geldi', value: num(summary.present) },
    { label: 'Gelmedi', value: num(summary.absent), tone: summary.absent ? 'bad' : '' },
    { label: 'Geç kaldı', value: num(summary.late), tone: summary.late ? 'warn' : '' },
    { label: 'İzinli', value: num(summary.excused) },
    { label: 'Devam oranı', value: percent(summary.attendanceRatePercent) },
  ]), table([
    { key: 'date', label: 'Tarih', width: '150px', cell: (row) => stack(fmtDate(row.date), fmtTime(row.startsAt)) },
    { key: 'subject', label: 'Ders', width: 'minmax(0, 1.6fr)', cell: (row) => row.subject?.name || '—' },
    { key: 'status', label: 'Durum', cell: (row) => statusBadge(ATTENDANCE, row.status) },
    { key: 'note', label: 'Not', width: 'minmax(0, 1.6fr)', cell: (row) => [row.note,
      row.leaveRequestId ? 'izin bildirimiyle' : ''].filter(Boolean).join(' · ') || '—' },
  ], items, 'Bu ay yoklama kaydı yok')];
}

// ---------------------------------------------------------------- çalışma

export function studyTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  const filters = { from: todayIst(-29), to: todayIst(), subject: '', source: '', hasQuestions: false };
  const range = dateRange({ start: filters.from, end: filters.to, label: 'Tarih', onChange: (value) => {
    filters.from = value.start || filters.from;
    filters.to = value.end || filters.to;
    logs.reload();
  } });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => {
    filters.subject = value; logs.reload();
  }, { label: 'Ders' });
  app.lookups.options('subjects', { empty: 'Tüm dersler' }).then((options) => subjectSelect.options(options))
    .catch(() => {});
  const sourceSelect = selectBox(optionsOf(LOG_SOURCE, { empty: 'Tüm kayıt türleri' }), '', (value) => {
    filters.source = value; logs.reload();
  }, { label: 'Kayıt türü' });
  const logs = pagedList({
    app,
    path: `/v1/students/${id}/study-logs`,
    // `false` süzgeç gönderilmez: vekil onu "soru çözülmeyenler" diye iletirdi.
    query: () => ({ from: filters.from, to: filters.to, subject: filters.subject || undefined,
      source: filters.source ? [filters.source] : undefined, hasQuestions: filters.hasQuestions || undefined }),
    columns: [
      { key: 'date', label: 'Tarih', width: '130px', cell: (row) => stack(dayOf(row.startedAt),
        `${fmtTime(row.startedAt)}–${fmtTime(row.endedAt)}`) },
      { key: 'duration', label: 'Süre', align: 'num', cell: (row) => secondsText(row.durationSeconds) },
      { key: 'subject', label: 'Ders / konu', width: 'minmax(0, 1.8fr)', cell: (row) => stack(row.subject?.name, row.topic?.name) },
      { key: 'source', label: 'Kayıt', cell: (row) => stack(LOG_SOURCE[row.source] || row.source,
        row.actedBy || LOG_MODE[row.mode]) },
      { key: 'questions', label: 'Soru', cell: (row) => (row.correct + row.wrong + row.blank ? dqb(row) : '—') },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)', cell: (row) => row.note || '—' },
    ],
    empty: { title: 'Bu aralıkta çalışma kaydı yok', text: 'Tarih aralığını genişletin ya da süzgeçleri temizleyin.' },
  });

  const tabletRange = dateRange({ start: todayIst(-6), end: todayIst(), label: 'Aralık', onChange: () => tablet.reload() });
  const tablet = loader(() => {
    const { start, end } = tabletRange.get();
    return app.api.get(`/v1/students/${id}/tablet`, { from: start, to: end });
  }, renderTablet, { rows: 4, columns: 4 });
  ctx.dispose(() => { range.destroy(); tabletRange.destroy(); logs.destroy(); });

  const logFilters = h('div', 'kit-card-body');
  logFilters.append(toolbar(range.node, subjectSelect.node, sourceSelect.node,
    checkbox('Yalnız soru çözülenler', (on) => { filters.hasQuestions = on; logs.reload(); })));
  const logBox = card('Çalışma kayıtları', logFilters);
  logBox.append(logs.node);
  const tabletFilters = h('div', 'kit-card-body');
  tabletFilters.append(toolbar(tabletRange.node));
  const tabletBox = card('Tablet kullanımı', tabletFilters);
  tabletBox.append(tablet.node);
  node.append(logBox);
  logs.reload();
  // Tablet özelliği kurumda kapalıysa kart hiç çizilmez, istek gitmez.
  if (app.can('students_getTabletUsage')) {
    node.append(tabletBox);
    tablet.reload();
  }
  return { node };
}

function renderTablet(data) {
  const days = data.days || [];
  const total = days.reduce((sum, day) => sum + (Number(day.totalSeconds) || 0), 0);
  const block = data.block || {};
  const nodes = [kpiRow([
    { label: 'Bugün', value: secondsText(data.todaySeconds) },
    { label: 'Bu hafta', value: secondsText(data.weekSeconds) },
    { label: 'Seçili aralık', value: secondsText(total) },
    { label: 'Engel', value: block.blocked ? 'Engelli' : 'Yok', tone: block.blocked ? 'warn' : '' },
  ])];
  if (block.blocked) {
    nodes.push(alertBox(`Tablet engelli${block.blockedUntil ? ` (${fmtDateTime(block.blockedUntil)} tarihine kadar)` : ''}`
      + `${BLOCK_BY[block.by] ? ` · ${BLOCK_BY[block.by]} engeli` : ''}${block.reason ? ` · ${block.reason}` : ''}.`, 'warn'));
  }
  if (data.activeSession) {
    nodes.push(alertBox(`Açık oturum: ${data.activeSession.deviceName} · giriş ${fmtDateTime(data.activeSession.loginAt)}.`, 'info'));
  }
  const body = padded(h('h4', 'ak-stu-h', 'Günlük kullanım'),
    barChart(days.map((day) => ({ label: fmtDate(day.date), value: Number(day.totalSeconds) || 0,
      display: secondsText(day.totalSeconds) })), { max: 31 }));
  // Uygulama kırılımı aynı yükten toplanır (ek istek yok).
  const apps = new Map();
  for (const day of days) {
    for (const item of day.apps || []) {
      const entry = apps.get(item.package) || { label: item.label || item.package, value: 0 };
      entry.value += Number(item.seconds) || 0;
      apps.set(item.package, entry);
    }
  }
  const top = [...apps.values()].sort((a, b) => b.value - a.value).slice(0, 10)
    .map((entry) => ({ ...entry, display: secondsText(entry.value) }));
  if (top.length) body.append(h('h4', 'ak-stu-h', 'En çok kullanılan uygulamalar'), barChart(top, { max: 10 }));
  nodes.push(body, table([
    { key: 'device', label: 'Cihaz', width: 'minmax(0, 1.4fr)', cell: (row) => row.deviceName || '—' },
    { key: 'login', label: 'Giriş', cell: (row) => fmtDateTime(row.loginAt) },
    { key: 'end', label: 'Çıkış', cell: (row) => (row.endedAt ? fmtDateTime(row.endedAt) : badge('Açık', 'info')) },
    { key: 'reason', label: 'Neden', cell: (row) => row.endReason || '—' },
  ], data.sessions, 'Bu aralıkta oturum yok'));
  return nodes;
}

// --------------------------------------------------------------- talepler

export function requestsTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  const requestsLink = (label, key) => (app.has('requests')
    ? button(label, { variant: 'ghost', onClick: () => app.go('requests', { tab: key }) }) : null);
  const etut = pagedList({
    app,
    path: `/v1/students/${id}/etut-requests`,
    limit: 10,
    columns: [
      { key: 'subject', label: 'Ders / konu', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.subject?.name, row.topic?.name) },
      { key: 'days', label: 'Tercih günleri', cell: (row) => (row.preferredDays || []).map((day) => DAY_LABEL[day] || day)
        .join(', ') || '—' },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.2fr)', cell: (row) => row.note || row.declineReason || '—' },
      { key: 'status', label: 'Durum', cell: (row) => (row.status === 'scheduled' && row.scheduledStartsAt
        ? badge(`Zaman verildi · ${fmtDateTime(row.scheduledStartsAt)}`, 'good') : statusBadge(REQ_STATUS, row.status)) },
      { key: 'at', label: 'İstendi', cell: (row) => dayOf(row.createdAt) },
    ],
    empty: { title: 'Etüt talebi yok' },
  });
  const meetings = pagedList({
    app,
    path: `/v1/students/${id}/meeting-requests`,
    limit: 10,
    columns: [
      { key: 'topic', label: 'Konu', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.topic,
        [MEETING_MODES[row.mode], row.note].filter(Boolean).join(' · ')) },
      { key: 'slot', label: 'Tercih / randevu', width: 'minmax(0, 1.6fr)', cell: (row) => (row.scheduled?.startsAt
        ? `${fmtDateTime(row.scheduled.startsAt)}${row.scheduled.location ? ` · ${row.scheduled.location}` : ''}`
        : (row.preferredSlots || []).map((slot) => `${fmtDate(slot.date)} ${slot.from}–${slot.to}`).join(' · ') || '—') },
      { key: 'by', label: 'İsteyen', cell: (row) => stack(personName(row.createdBy), dayOf(row.createdAt)) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(REQ_STATUS, row.status) },
    ],
    empty: { title: 'Görüşme talebi yok' },
  });
  const books = pagedList({
    app,
    path: `/v1/students/${id}/book-requests`,
    limit: 10,
    columns: [
      { key: 'ref', label: 'Talep', width: '110px' },
      { key: 'product', label: 'Kitap', width: 'minmax(0, 2fr)', cell: (row) => stack(row.product?.title,
        [row.product?.publisher, row.product?.isbn].filter(Boolean).join(' · ')) },
      { key: 'total', label: 'Tutar', align: 'num', cell: (row) => money(row.totalMinor
        ?? (row.product?.priceMinor || 0) * (row.quantity || 1)) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(BOOK_STATUS, row.status) },
      { key: 'at', label: 'İstendi', cell: (row) => dayOf(row.requestedAt) },
    ],
    onRow: (row) => bookRequest(ctx, row.id),
    empty: { title: 'Kitap talebi yok' },
  });
  ctx.dispose(() => { etut.destroy(); meetings.destroy(); books.destroy(); });
  const showBooks = app.can('requests_listBookRequests');
  node.append(...[
    h('div', 'ak-muted', 'Onay, ret ve zaman verme Talepler ile Kitap talepleri ekranlarından yapılır. İzin '
      + 'bildirimleri Yoklama sekmesindedir.'),
    panelCard('Etüt talepleri', etut.node, [requestsLink('Talepler ekranı', 'etut')]),
    panelCard('Görüşme talepleri', meetings.node, [requestsLink('Talepler ekranı', 'meeting')]),
    // Kitap talepleri kurumda kapalıysa kart hiç çizilmez, istek gitmez.
    showBooks ? panelCard('Kitap talepleri', books.node, [button('Kitap talepleri ekranı', { variant: 'ghost',
      onClick: () => app.go('books') })]) : null,
  ].filter(Boolean));
  etut.reload();
  meetings.reload();
  if (showBooks) books.reload();
  return { node };
}

function bookRequest(ctx, bookRequestId) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: 'Kitap talebi' });
  const view = loader(() => app.api.get(`/v1/students/${id}/book-requests/${bookRequestId}`), (request) => {
    panel.setTitle(`${request.ref} — ${request.product?.title || ''}`);
    const nodes = [];
    if (request.status === 'order_failed') {
      nodes.push(alertBox(`bbdstore siparişi oluşturulamadı${request.order?.failureCode
        ? ` (${request.order.failureCode})` : ''}. Yeniden denemek için Kitap talepleri ekranını açın.`, 'bad'));
    }
    nodes.push(card('', kv([
      ['Kitap', request.product?.title],
      ['Yayınevi / ISBN', [request.product?.publisher, request.product?.isbn].filter(Boolean).join(' · ') || '—'],
      ['Adet × fiyat', `${num(request.quantity)} × ${money(request.product?.priceMinor)}`],
      ['Toplam', money(request.totalMinor ?? (request.product?.priceMinor || 0) * (request.quantity || 1))],
      ['Durum', statusBadge(BOOK_STATUS, request.status)],
      ['Not', request.note],
      ['İsteyen', `${personName(request.requestedBy)} · ${fmtDateTime(request.requestedAt)}`],
      ['Veli kararı', request.decision?.decidedAt ? [personName(request.decision.by),
        BOOK_PAYMENT[request.decision.paymentChoice] || request.decision.paymentChoice,
        request.decision.rejectReason].filter(Boolean).join(' · ') : '—'],
      ['Sipariş', request.order?.incrementId ? `#${request.order.incrementId} · ${request.order.status || ''}` : '—'],
      ['Teslim', fmtDateTime(request.deliveredAt)],
    ])));
    nodes.push(toolbar(button('Kitap talepleri ekranında aç', { variant: 'primary', onClick: () => {
      panel.close();
      app.go('books', { bookRequestId: request.id });
    } })));
    const events = [...(request.history || [])].sort((a, b) => String(a.at).localeCompare(String(b.at)))
      .map((item) => ({ title: BOOK_STATUS[item.status]?.[0] || item.status, at: fmtDateTime(item.at),
        detail: item.actorLabel || '', tone: BOOK_STATUS[item.status]?.[1] || '' }));
    nodes.push(card('Geçmiş', timeline(events, { emptyText: 'Henüz hareket yok.' })));
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

// ------------------------------------------------------------ kurum cari

export function financeTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack ak-money-finance ak-stu-money');
  node.append(toolbar(h('span', 'ak-muted', 'Kurum cari hesabı: taksit, kalem ve tahsilat. Kantin hesabı bu sekmede yer almaz.'),
    h('span', 'kit-spacer'), contractEntry(app, { studentId: id }), button('Ekstre', { onClick: () => statement(ctx) }),
    button('Cari hesabı aç', { variant: 'primary', onClick: () => app.go('finance', { studentId: id }) })));
  const summary = loader(() => app.api.get(`/v1/students/${id}/finance`), renderFinanceSummary, { rows: 3, columns: 4 });
  let chargeStatus = '';
  const statusSelect = selectBox(optionsOf(CHARGE_STATUS, { empty: 'Tüm kalemler' }), '', (value) => {
    chargeStatus = value; charges.reload();
  }, { label: 'Kalem durumu' });
  const charges = pagedList({
    app,
    path: `/v1/students/${id}/finance/charges`,
    query: () => ({ status: chargeStatus || undefined }),
    limit: 20,
    columns: [
      { key: 'due', label: 'Vade', width: '96px', cell: (row) => fmtDate(row.dueDate) },
      { key: 'description', label: 'Açıklama', width: 'minmax(0, 2fr)', cell: (row) => stack(row.description,
        [CHARGE_KINDS[row.kind], row.installmentNo ? `${row.installmentNo}/${row.installmentCount}` : '',
          row.cancelReason].filter(Boolean).join(' · ')) },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      { key: 'paid', label: 'Ödenen', align: 'num', cell: (row) => money(row.paidMinor) },
      { key: 'remaining', label: 'Kalan', align: 'num', cell: (row) => money(row.remainingMinor) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(CHARGE_STATUS, row.status) },
    ],
    empty: { title: 'Bu durumda kalem yok' },
  });
  const payments = pagedList({
    app,
    path: `/v1/students/${id}/finance/payments`,
    limit: 20,
    columns: [
      { key: 'receiptNo', label: 'Makbuz', width: '120px' },
      { key: 'paidAt', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.paidAt) },
      { key: 'method', label: 'Yöntem', cell: (row) => METHODS[row.method] || row.method },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(PAYMENT_STATUS, row.status) },
      { key: 'actions', label: '', width: '100px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button('Makbuz', { variant: 'ghost', onClick: () => receipt(ctx, row.id) }));
        return actions;
      } },
    ],
    empty: { title: 'Henüz tahsilat yok' },
  });
  ctx.dispose(() => { charges.destroy(); payments.destroy(); });
  node.append(card('Özet', summary.node), panelCard('Borç kalemleri ve vadeler', charges.node, [statusSelect.node]),
    card('Tahsilat geçmişi', payments.node));
  summary.reload();
  charges.reload();
  payments.reload();
  return { node };
}

function renderFinanceSummary(data) {
  const next = data.nextDue || {};
  const last = data.lastPayment || {};
  return [kpiRow([
    { label: 'Açık bakiye', value: money(data.openBalanceMinor) },
    { label: 'Gecikmiş', value: money(data.overdueMinor), tone: data.overdueMinor ? 'bad' : '' },
    { label: '30 gün içinde', value: money(data.dueNext30DaysMinor) },
    { label: 'Alacak', value: money(data.creditMinor), title: 'Fazla ödeme; sonraki kalemlere mahsup edilir.' },
  ]), kv([
    ['Sonraki vade', next.dueDate ? `${fmtDate(next.dueDate)} · ${next.description || ''} · ${money(next.remainingMinor)}` : '—'],
    ['Son tahsilat', last.paidAt ? `${fmtDateTime(last.paidAt)} · ${money(last.amountMinor)} · ${METHODS[last.method] || ''}` : '—'],
    ['Uygulamadan kartla ödeme', data.cardPaymentEnabled ? 'Açık' : 'Kapalı'],
  ]), table([
    { key: 'title', label: 'Taksit planı', width: 'minmax(0, 2fr)' },
    { key: 'total', label: 'Toplam', align: 'num', cell: (row) => money(row.totalMinor) },
    { key: 'paid', label: 'Ödenen taksit', align: 'num', cell: (row) => `${num(row.paidCount)} / ${num(row.installmentCount)}` },
  ], data.plans, 'Taksit planı yok')];
}

function receipt(ctx, paymentId) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: 'Makbuz', subtitle: 'Kurum cari tahsilatı' });
  const view = loader(() => app.api.get(`/v1/students/${id}/finance/payments/${paymentId}/receipt`), (data) => {
    const payment = data.payment || {};
    panel.setTitle(`Makbuz ${data.receiptNo}`);
    const nodes = [];
    if (payment.status === 'voided') {
      nodes.push(alertBox(`Bu tahsilat ${fmtDateTime(payment.voidedAt)} tarihinde iptal edildi: ${payment.voidReason || ''}`, 'warn'));
    }
    const box = card('', kv([
      ['Kurum', data.institution?.name],
      ['Öğrenci', `${data.student?.displayName || ''} · ${data.student?.externalRef || ''}`],
      ['Tarih', fmtDateTime(payment.paidAt)],
      ['Tutar', money(payment.amountMinor)],
      ['Yöntem', METHODS[payment.method] || payment.method],
      ['Referans', payment.reference || payment.ref],
      ['Kaydeden', personName(payment.recordedBy)],
      ['Not', payment.note],
      ['İşlem sonrası bakiye', money(data.balanceAfterMinor)],
      ['İşlem sonrası alacak', money(data.creditAfterMinor)],
    ]));
    box.classList.add('ak-money-finance');
    nodes.push(box, card('Dağıtım', table([
      { key: 'description', label: 'Kapanan kalem', width: 'minmax(0, 2fr)' },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    ], payment.allocations, 'Kaleme dağıtılmadı')));
    if (data.pdfUrl) {
      nodes.push(toolbar(button('PDF / yazdır', { variant: 'primary', onClick: () => runAction(app, async () => {
        openBlob(await app.api.fetchFile(data.pdfUrl, `${data.receiptNo}.pdf`));
      }) })));
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

function statement(ctx) {
  const { app, id } = ctx;
  let range = null;
  const panel = subDrawer(ctx, { title: 'Hesap ekstresi', subtitle: 'Kurum cari', onClose: () => range?.destroy() });
  const state = { from: todayIst(-365), to: todayIst() };
  range = dateRange({ start: state.from, end: state.to, label: 'Dönem', onChange: (value) => {
    state.from = value.start || state.from;
    state.to = value.end || state.to;
    view.reload();
  } });
  const pdf = button('PDF / yazdır', { onClick: () => runAction(app, async () => {
    const data = await app.api.get(`/v1/students/${id}/finance/statement`, { from: state.from, to: state.to, format: 'pdf' });
    if (!data?.pdfUrl) throw new AkademiError({ message: 'Akademi bu ekstre için PDF üretmedi.' });
    openBlob(await app.api.fetchFile(data.pdfUrl, `ekstre-${state.from}-${state.to}.pdf`));
  }) });
  const view = loader(() => app.api.get(`/v1/students/${id}/finance/statement`, { from: state.from, to: state.to }),
    (data) => [kpiRow([
      { label: 'Dönem başı', value: money(data.openingBalanceMinor) },
      { label: 'Dönem sonu', value: money(data.closingBalanceMinor) },
      { label: 'Alacak', value: money(data.creditMinor) },
    ]), table([
      { key: 'date', label: 'Tarih', width: '96px', cell: (row) => fmtDate(row.date) },
      { key: 'kind', label: 'Hareket', cell: (row) => STATEMENT_KIND[row.kind] || row.kind },
      { key: 'description', label: 'Açıklama', width: 'minmax(0, 2fr)' },
      { key: 'charge', label: 'Borç', align: 'num', cell: (row) => (row.chargeMinor ? money(row.chargeMinor) : '') },
      { key: 'payment', label: 'Ödeme', align: 'num', cell: (row) => (row.paymentMinor ? money(row.paymentMinor) : '') },
      { key: 'balance', label: 'Bakiye', align: 'num', cell: (row) => money(row.balanceMinor) },
    ], data.lines, 'Bu dönemde hareket yok')]);
  panel.node.classList.add('ak-money-finance');
  panel.body.append(toolbar(range.node, h('span', 'kit-spacer'), pdf), view.node);
  view.reload();
}

// ----------------------------------------------------------------- kantin

export function canteenTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack ak-money-canteen ak-stu-money');
  // Kantin hesabı kurum carisinden tamamen ayrıdır (A12) ve burada salt okunurdur.
  const hint = kmManagedHint(ctx, 'Kantin hesabı kurum carisinden ayrıdır, burada yalnız görüntülenir. '
    + 'Harcama limiti, engel, ürün kısıtı ve kantin tahsilatı', ctx.student?.student || { id },
  { tab: 'canteen' });
  const summary = loader(() => app.api.get(`/v1/students/${id}/canteen`), renderCanteenSummary, { rows: 4, columns: 3 });
  const controls = loader(() => app.api.get(`/v1/admin/students/${id}/canteen/controls`), renderCanteenControls,
    { rows: 4, columns: 2 });
  const range = dateRange({ start: todayIst(-29), end: todayIst(), label: 'Tarih', onChange: () => {
    transactions.reload();
    collections.reload();
  } });
  const rangeQuery = () => {
    const { start, end } = range.get();
    return { from: start || undefined, to: end || undefined };
  };
  const transactions = pagedList({
    app,
    path: `/v1/students/${id}/canteen/transactions`,
    query: rangeQuery,
    limit: 20,
    columns: [
      { key: 'at', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.at) },
      { key: 'items', label: 'Ürünler', width: 'minmax(0, 2.4fr)', cell: (row) => (row.items || [])
        .map((item) => `${num(item.qty)}× ${item.name}`).join(', ') || '—' },
      { key: 'total', label: 'Tutar', align: 'num', cell: (row) => money(row.totalMinor) },
      { key: 'reversed', label: 'Durum', cell: (row) => (row.reversed ? badge('İade edildi', 'dim') : '—') },
    ],
    empty: { title: 'Bu aralıkta kantin harcaması yok' },
  });
  const collections = pagedList({
    app,
    path: `/v1/students/${id}/canteen/collections`,
    query: rangeQuery,
    limit: 20,
    columns: [
      { key: 'at', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.at) },
      { key: 'method', label: 'Yöntem', cell: (row) => CANTEEN_METHOD[row.method] || row.method },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      { key: 'ref', label: 'Referans', cell: (row) => row.ref || '—' },
    ],
    empty: { title: 'Bu aralıkta kantin tahsilatı yok' },
  });
  let prStatus = '';
  const prSelect = selectBox(optionsOf(PR_STATUS, { empty: 'Tüm talepler' }), '', (value) => {
    prStatus = value; requests.reload();
  }, { label: 'Talep durumu' });
  const requests = pagedList({
    app,
    path: `/v1/students/${id}/canteen/payment-requests`,
    query: () => ({ status: prStatus || undefined }),
    limit: 20,
    columns: [
      { key: 'ref', label: 'Talep', width: '120px' },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(PR_STATUS, row.status) },
      { key: 'origin', label: 'Kaynak', cell: (row) => stack(PR_ORIGIN[row.origin] || row.origin,
        row.amountFixed ? 'tutar sabit' : 'veli tutarı değiştirebilir') },
      { key: 'created', label: 'Oluşturuldu', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'paid', label: 'Ödendi / son gün', cell: (row) => (row.paidAt ? fmtDateTime(row.paidAt)
        : (row.expiresAt ? `son ${fmtDateTime(row.expiresAt)}` : '—')) },
    ],
    empty: { title: 'Kantin ödeme talebi yok' },
  });
  ctx.dispose(() => { range.destroy(); transactions.destroy(); collections.destroy(); requests.destroy(); });
  const rangeBar = h('div', 'kit-card-body');
  rangeBar.append(toolbar(range.node));
  const spendBox = card('Harcamalar', rangeBar);
  spendBox.append(transactions.node);
  node.append(hint, grid2(card('Kantin özeti', summary.node), card('Kantin kontrolleri (veli + kurum)', controls.node)),
    spendBox, card('Kantin tahsilatları (nakit + kart)', collections.node),
    panelCard('Kantin ödeme talepleri', requests.node, [prSelect.node]));
  summary.reload();
  controls.reload();
  transactions.reload();
  collections.reload();
  requests.reload();
  return { node };
}

function renderCanteenSummary(data) {
  const nodes = [];
  if (data.stale) nodes.push(alertBox(`Kantin verisi güncel olmayabilir (son eşitleme ${fmtDateTime(data.updatedAt)}).`, 'warn'));
  if (data.isBlocked) nodes.push(alertBox('Kantin kurum tarafından engelli.', 'warn'));
  else if (data.guardianBlocked) nodes.push(alertBox('Kantin veli tarafından engelli.', 'warn'));
  const hidden = Object.entries(data.hidden || {}).filter(([, on]) => on).map(([key]) => CANTEEN_HIDDEN[key] || key);
  nodes.push(kpiRow([
    { label: 'Bakiye', value: money(data.balanceMinor), tone: Number(data.balanceMinor) < 0 ? 'bad' : '' },
    { label: 'Kantin borcu', value: money(data.debtMinor), tone: data.debtMinor ? 'bad' : '' },
    { label: 'Bugün', value: money(data.spentTodayMinor) },
    { label: 'Bu ay', value: money(data.monthSpentMinor) },
  ]), kv([
    ['Kalan limit', money(data.remainingLimitMinor)],
    ['Harcama limiti', isSet(data.spendingLimitMinor) ? money(data.spendingLimitMinor) : 'Limitsiz'],
    ['Günlük limit', isSet(data.dailyLimitMinor) ? money(data.dailyLimitMinor) : 'Limitsiz'],
    ['Ön ödeme alacağı', money(data.creditMinor)],
    ['Ön ödeme', data.allowPrepayment ? `Açık · en çok ${money(data.maxPrepaymentMinor)}` : 'Kapalı'],
    ['Bekleyen ödeme talebi', num(data.pendingPaymentRequestCount)],
    ['Uygulamada gizlenen', hidden.join(', ') || '—'],
    ['Son güncelleme', fmtDateTime(data.updatedAt)],
  ]));
  return nodes;
}

function renderCanteenControls(data) {
  return kv([
    ['Harcama limiti', isSet(data.spendingLimitMinor) ? money(data.spendingLimitMinor) : 'Limitsiz'],
    ['Günlük limit', isSet(data.dailyLimitMinor) ? money(data.dailyLimitMinor) : 'Limitsiz'],
    ['Kurum engeli', data.isBlocked ? badge('Engelli', 'bad') : 'Yok'],
    ['Veli engeli', data.guardianBlocked ? badge('Engelli', 'warn') : 'Yok'],
    ['Kısıtlı ürünler', (data.restrictedProducts || []).map((product) => product.name).join(', ') || 'Yok'],
    ['Velinin koyabileceği en yüksek limit', isSet(data.maxGuardianLimitMinor) ? money(data.maxGuardianLimitMinor) : 'Sınırsız'],
    ['Veli engelleyebilir', data.guardianCanBlock ? 'Evet' : 'Hayır'],
    ['Bugün harcanan', money(data.spentTodayMinor)],
    ['Son değişiklik', fmtDateTime(data.controlsUpdatedAt)],
  ]);
}
