// BBD Akademi — Değerlendirme listesi (taslak + yayımlanmış).
//
// Süzgeçler: dönem, sınıf, durum ve tek öğrenci. Liste imleçlidir; satırdaki
// okunma sayısı liste yükünden gelir (satır başına istek yok). Çekmecede bir
// değerlendirme kaydedilince / yayımlanınca satır yerinde güncellenir.

import { h } from '../../ui-kit/kit.js';
import {
  fmtDateTime, num, optionsOf, pagedList, selectBox, statusBadge, studentPicker, toolbar,
} from './common.js';
import { EVALUATION_STATUS, averageText, removableChip, stackCell, studentCell } from './evaluations-shared.js';
import { summaryOf } from './evaluations-form.js';

export function evaluationsTab(ctx) {
  const { app } = ctx;
  const node = h('div', 'ak-stack');
  const filters = { periodId: '', classGroupId: '', status: '', studentId: '', studentName: '' };

  const periodSelect = selectBox([{ value: '', label: 'Bütün dönemler' }], '', (value) => {
    filters.periodId = value; list.reload();
  }, { label: 'Dönem' });
  const classSelect = selectBox([{ value: '', label: 'Bütün sınıflar' }], '', (value) => {
    filters.classGroupId = value; list.reload();
  }, { label: 'Sınıf' });
  const statusSelect = selectBox(optionsOf(EVALUATION_STATUS, { empty: 'Bütün durumlar' }), '', (value) => {
    filters.status = value; list.reload();
  }, { label: 'Durum' });
  const studentSlot = h('div', 'ak-ev-student-filter');
  const picker = app.can('admin_listStudents')
    ? studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => setStudent(student.id,
      student.displayName || 'Öğrenci') })
    : null;
  node.append(toolbar(periodSelect.node, classSelect.node, statusSelect.node, studentSlot));

  function paintStudent() {
    studentSlot.replaceChildren();
    if (filters.studentId) {
      studentSlot.append(removableChip(`Öğrenci: ${filters.studentName || 'seçili'}`,
        () => setStudent('', ''), 'Öğrenci süzgecini kaldır'));
    } else if (picker) {
      studentSlot.append(picker.node);
    }
  }

  function setStudent(studentId, name) {
    filters.studentId = studentId;
    filters.studentName = name;
    paintStudent();
    list.reload();
  }

  const list = pagedList({
    app,
    path: '/v1/admin/evaluations',
    query: () => ({
      periodId: filters.periodId || undefined,
      classGroupId: filters.classGroupId || undefined,
      status: filters.status || undefined,
      studentId: filters.studentId || undefined,
    }),
    limit: 50,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.5fr)', cell: (row) => studentCell(row.student) },
      { key: 'period', label: 'Dönem', width: 'minmax(0, 1.4fr)', cell: (row) => stackCell(row.period?.name, row.title) },
      { key: 'status', label: 'Durum', width: '110px', cell: (row) => statusBadge(EVALUATION_STATUS, row.status) },
      { key: 'average', label: 'Ortalama', align: 'num', width: '86px', cell: (row) => averageText(row.average) },
      { key: 'evaluator', label: 'Değerlendiren', width: 'minmax(0, 1fr)',
        cell: (row) => stackCell(row.evaluator?.displayName || '—',
          row.status === 'published' ? `yayım ${fmtDateTime(row.publishedAt)}` : `kayıt ${fmtDateTime(row.updatedAt)}`) },
      { key: 'reads', label: 'Okunma', align: 'num', width: '80px',
        title: 'Öğrenci ve velilerin okuma sayısı',
        cell: (row) => (row.status === 'published' ? num(row.readCount || 0) : '—') },
    ],
    onRow: (row) => ctx.openEvaluation(row.id),
    empty: { title: 'Bu süzgece uyan değerlendirme yok',
      text: 'Süzgeçleri değiştirin ya da dönemler sekmesinden öğrencileri değerlendirmeye başlayın.' },
  });
  node.append(list.node);

  function matches(evaluation) {
    return (!filters.periodId || evaluation.period?.id === filters.periodId)
      && (!filters.classGroupId || evaluation.student?.classGroup?.id === filters.classGroupId)
      && (!filters.status || evaluation.status === filters.status)
      && (!filters.studentId || evaluation.student?.id === filters.studentId);
  }

  let started = false;
  const offEvents = ctx.on((event) => {
    if (!started) return;
    if (event.type === 'period') { fillPeriods(); return; }
    if (event.type !== 'evaluation' || !event.evaluation) return;
    const { evaluation } = event;
    const same = (row) => row.id === evaluation.id;
    if (event.kind === 'deleted' || !matches(evaluation)) { list.remove(same); return; }
    if (event.kind === 'created' || !list.rows().some(same)) { list.reload(); return; }
    list.replace(same, summaryOf(evaluation));
  });

  /** Dönem yazan taraf `ctx.periods(true)` ile tazelemeyi zaten başlatır; burada beklenir. */
  async function fillPeriods() {
    periodSelect.options(await ctx.periodOptions({ empty: 'Bütün dönemler' }));
  }

  async function fillFilters() {
    await Promise.all([
      fillPeriods().catch(() => {}),
      ctx.loadNames().then(() => classSelect.options([{ value: '', label: 'Bütün sınıflar' },
        ...ctx.classList.map((item) => ({ value: item.id, label: item.name }))])).catch(() => {}),
    ]);
  }

  paintStudent();
  return {
    node,
    show() {
      if (started) return;
      started = true;
      fillFilters();
      list.reload();
    },
    /** Dış bağlantı: süzgeçleri kurup listeyi tazeler. */
    filter({ periodId, studentId, studentName } = {}) {
      started = true;
      if (periodId !== undefined) { filters.periodId = periodId || ''; periodSelect.set(filters.periodId); }
      if (studentId !== undefined) { filters.studentId = studentId || ''; filters.studentName = studentName || ''; }
      paintStudent();
      fillFilters().then(() => periodSelect.set(filters.periodId));
      list.reload();
    },
    destroy() { offEvents(); list.destroy(); picker?.destroy?.(); },
  };
}
