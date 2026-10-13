// BBD Akademi — Değerlendirme dönemleri (F-A21: dönem + tamamlama durumu).
//
// Dönem = şablon + hedef (sınıf / çalışma grubu / öğrenci; boşsa kurumun
// bütün aktif öğrencileri) + başlangıç ve son tarih + paylaşım varsayılanı.
// Başlangıç günü gelince Akademi dönemi `open` yapar. Ayrıntıda tamamlama
// sayıları (`progress`) ve öğrenci listesi (değerlendirildi mi) durur;
// satırdan "Değerlendir" taslak açar ya da mevcut değerlendirmeyi gösterir.
//
// Akademi kuralları: değerlendirme başlamışsa şablon değişmez (409
// `period_started`), hedef genişletilebilir; kapanan dönemde yeni
// değerlendirme açılmaz ama taslaklar yayımlanabilir; yalnız hiç
// değerlendirmesi olmayan dönem silinir (409 `period_has_evaluations`).
// Değerlendirme kaydedilince satır yerinde güncellenir, dönem özeti tek
// okumayla tazelenir (satır başına istek yok).

import { blockedButton, button, debounce, h } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow, kpiRow } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, fmtDate, fmtDateTime, formDialog, kv, multiChips, num, pagedList, percent,
  problem, runAction, selectBox, statusBadge, studentPicker, todayIst, addDays, toolbar,
} from './common.js';
import {
  PERIOD_STATUS, PROGRESS_STATUS, averageText, exportReports, progressCell, removableChip, rowActions,
  shareText, stackCell, studentCell, targetText,
} from './evaluations-shared.js';
import { documentButtons } from './documents.js';
import { mapLimit, reportCardDoc } from './documents-logic.js';

/** Dönemler sekmesi: liste ⇄ dönem ayrıntısı. */
export function periodsTab(ctx) {
  const { app } = ctx;
  const node = h('div', 'ak-stack');
  const listView = h('div', 'ak-stack');
  const detailView = h('div', 'ak-stack');
  detailView.hidden = true;
  node.append(listView, detailView);
  const filters = { status: 'all', classGroupId: '' };
  let detail = null;
  let stale = false;

  const add = actionButton(app, 'admin_createEvaluationPeriod', 'Dönem aç', { variant: 'primary',
    onClick: () => createPeriod() });
  const statusChips = chipRow([
    { key: 'all', label: 'Tümü' },
    { key: 'open', label: 'Açık' },
    { key: 'scheduled', label: 'Planlanan' },
    { key: 'closed', label: 'Kapanan' },
  ], filters.status, (key) => { filters.status = key || 'all'; statusChips.set(filters.status); list.reload(); });
  const classSelect = selectBox([{ value: '', label: 'Bütün sınıflar' }], '', (value) => {
    filters.classGroupId = value; list.reload();
  }, { label: 'Sınıf' });
  listView.append(toolbar(statusChips.node, classSelect.node, h('span', 'kit-spacer'), add));

  const list = pagedList({
    app,
    path: '/v1/admin/evaluation-periods',
    query: () => ({ status: filters.status, classGroupId: filters.classGroupId || undefined }),
    limit: 50,
    columns: [
      { key: 'name', label: 'Dönem', width: 'minmax(0, 1.8fr)',
        cell: (row) => stackCell(row.name, `${row.template?.name || '—'} · v${row.template?.version ?? '—'}`) },
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.4fr)', cell: (row) => targetText(row.target, ctx.names) },
      { key: 'dates', label: 'Tarih', width: '170px',
        cell: (row) => stackCell(`${fmtDate(row.startDate)} – ${fmtDate(row.dueDate)}`, dueText(row)) },
      { key: 'status', label: 'Durum', width: '100px', cell: (row) => statusBadge(PERIOD_STATUS, row.status) },
      { key: 'progress', label: 'Tamamlama', width: 'minmax(150px, 1fr)', cell: (row) => progressCell(row.progress) },
    ],
    onRow: (row) => openPeriod(row.id, row),
    empty: { title: 'Bu süzgece uyan dönem yok',
      text: 'Değerlendirme dönemi açın (ör. “Ekim değerlendirmesi”): şablon, hedef sınıflar ve son tarih.' },
  });
  listView.append(list.node);

  ctx.loadNames().then(() => {
    classSelect.options([{ value: '', label: 'Bütün sınıflar' },
      ...ctx.classList.map((item) => ({ value: item.id, label: item.name }))]);
    list.table.update({ rows: list.rows() });     // hedef sütunu adlarla yeniden çizilsin
  }).catch(() => {});

  const offEvents = ctx.on((event) => {
    if (event.type === 'evaluation') stale = true;
    if (event.type === 'period' && event.period) {
      list.replace((row) => row.id === event.period.id, event.period);
    }
  });

  // ------------------------------------------------------------ yazma
  async function createPeriod() {
    const saved = await periodDialog(ctx, null);
    if (!saved) return;
    app.toast(saved.status === 'open' ? 'Dönem açıldı; değerlendirmeye başlanabilir.'
      : `Dönem planlandı; ${fmtDate(saved.startDate)} günü açılır.`, 'good');
    ctx.periods(true).catch(() => {});
    ctx.emit({ type: 'period', kind: 'created' });
    list.reload();
    openPeriod(saved.id, saved);
  }

  // ----------------------------------------------------------- ayrıntı
  function openPeriod(periodId, initial = null) {
    detail?.destroy();
    detail = periodDetail(ctx, periodId, initial, {
      onBack: () => closeDetail(),
      onChanged: (period) => list.replace((row) => row.id === period.id, period),
      onDeleted: (id) => { list.remove((row) => row.id === id); closeDetail(); },
    });
    detailView.replaceChildren(detail.node);
    detailView.hidden = false;
    listView.hidden = true;
  }

  function closeDetail() {
    detail?.destroy();
    detail = null;
    detailView.replaceChildren();
    detailView.hidden = true;
    listView.hidden = false;
    if (stale) { stale = false; list.reload(); }
  }

  let started = false;
  return {
    node,
    show() { if (!started) { started = true; list.reload(); } },
    open: (periodId) => { started = true; openPeriod(periodId); },
    back: () => { if (detail) closeDetail(); },
    destroy() { offEvents(); list.destroy(); detail?.destroy(); },
  };
}

function dueText(period) {
  if (period.status === 'closed') return period.closedAt ? `kapandı ${fmtDate(period.closedAt)}` : 'kapandı';
  const today = todayIst();
  if (period.status === 'scheduled') return `${fmtDate(period.startDate)} günü açılır`;
  if (period.dueDate < today) return 'son tarih geçti';
  if (period.dueDate === today) return 'son gün bugün';
  return '';
}

/** Dönem ayrıntısı: özet + tamamlama + öğrenci listesi. */
function periodDetail(ctx, periodId, initial, { onBack, onChanged, onDeleted }) {
  const { app } = ctx;
  const node = h('div', 'ak-stack');
  const headSlot = h('div', 'ak-stack');
  const summarySlot = h('div', 'ak-stack');
  let period = initial;
  let classGroupId = '';
  let studentStatus = '';
  let token = 0;

  const back = button('← Dönemler', { variant: 'ghost', onClick: onBack });
  const classSelect = selectBox([{ value: '', label: 'Bütün sınıflar' },
    ...ctx.classList.map((item) => ({ value: item.id, label: item.name }))], '', (value) => {
    classGroupId = value; refresh(); students.reload();
  }, { label: 'Sınıf' });
  const chips = chipRow([
    { key: 'not_started', label: 'Başlanmadı', count: '' },
    { key: 'draft', label: 'Taslak', count: '' },
    { key: 'published', label: 'Yayımlandı', count: '' },
  ], null, (key) => { studentStatus = key || ''; students.reload(); });

  const students = pagedList({
    app,
    path: `/v1/admin/evaluation-periods/${periodId}/students`,
    query: () => ({ status: studentStatus || undefined, classGroupId: classGroupId || undefined }),
    limit: 50,
    rowKey: (row) => String(row.student?.id || ''),
    // Seçim toplu karne içindir: seçilenlerin karneleri tek baskı işi olur.
    selectable: app.can('admin_getEvaluation'),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => studentCell(row.student) },
      { key: 'status', label: 'Durum', width: '110px', cell: (row) => statusBadgeOf(row.status) },
      { key: 'average', label: 'Ortalama', align: 'num', width: '90px', cell: (row) => averageText(row.average) },
      { key: 'evaluator', label: 'Değerlendiren', width: 'minmax(0, 1fr)',
        cell: (row) => stackCell(row.evaluator?.displayName || '—', row.updatedAt ? fmtDateTime(row.updatedAt) : '') },
      { key: 'action', label: '', width: '130px', cell: (row) => rowActions(rowButton(row)) },
    ],
    onRow: (row) => (row.evaluationId ? ctx.openEvaluation(row.evaluationId) : evaluate(row)),
    empty: { title: 'Bu süzgece uyan öğrenci yok', text: 'Durum ya da sınıf süzgecini değiştirin.' },
  });

  function statusBadgeOf(status) { return statusBadge(PROGRESS_STATUS, status); }

  function rowButton(row) {
    if (row.evaluationId) {
      return button(row.status === 'published' ? 'Görüntüle' : 'Devam et', { variant: 'ghost',
        onClick: () => ctx.openEvaluation(row.evaluationId) });
    }
    if (period?.status === 'closed') return blockedButton('Değerlendir', 'Dönem kapandı; yeni değerlendirme açılamaz.');
    if (period?.status === 'scheduled') {
      return blockedButton('Değerlendir', `Dönem ${fmtDate(period.startDate)} günü açılır.`);
    }
    return actionButton(app, 'admin_createEvaluation', 'Değerlendir', { variant: 'primary',
      onClick: () => evaluate(row) });
  }

  function evaluate(row) {
    if (period?.status !== 'open') {
      app.toast(period?.status === 'closed' ? 'Dönem kapandı; yeni değerlendirme açılamaz.'
        : 'Dönem henüz açılmadı.', 'warn');
      return;
    }
    ctx.createEvaluation({ studentId: row.student.id, periodId, student: row.student });
  }

  /** Sıradaki öğrenci: `status=not_started` süzgeciyle tek satır. */
  async function nextStudent(control) {
    const data = await runAction(app, () => app.api.get(`/v1/admin/evaluation-periods/${periodId}/students`,
      { status: 'not_started', classGroupId: classGroupId || undefined, limit: 1 }), { button: control });
    const row = data?.items?.[0];
    if (!data) return;
    if (!row) { app.toast('Bu süzgeçte değerlendirilmemiş öğrenci kalmadı.', 'good'); return; }
    evaluate(row);
  }

  // Değerlendirme kaydı/yayını/silinmesi: satır yerinde, özet tek okumayla.
  const refreshSoon = debounce(() => refresh(), 400);
  const offEvents = ctx.on((event) => {
    if (event.type !== 'evaluation' || !event.evaluation) return;
    const evaluation = event.evaluation;
    if (evaluation.period?.id !== periodId) return;
    const match = (row) => row.student?.id === evaluation.student?.id;
    const current = students.rows().find(match);
    if (current) {
      students.replace(match, event.kind === 'deleted'
        ? { ...current, status: 'not_started', evaluationId: null, average: null, evaluator: null, updatedAt: null }
        : { ...current, status: evaluation.status, evaluationId: evaluation.id, average: evaluation.average,
          evaluator: evaluation.evaluator, updatedAt: evaluation.updatedAt || evaluation.createdAt });
    }
    refreshSoon();
  });

  /**
   * Toplu karne: seçili öğrenciler; seçim yoksa süzgeçteki YAYIMLANMIŞ bütün
   * değerlendirmeler. Akademi'ye aynı anda en çok 4 istek (A14), iptal edilebilir.
   */
  async function reportCards(progress, state) {
    let rows = students.table.selectedRows().filter((row) => row.evaluationId);
    if (!rows.length) {
      progress(0, 0, 'Değerlendirmeler listeleniyor…');
      rows = (await app.api.all(`/v1/admin/evaluation-periods/${periodId}/students`,
        { status: 'published', classGroupId: classGroupId || undefined }, { limit: 100, max: 1000 }))
        .filter((row) => row.evaluationId);
    }
    if (!rows.length || state.cancelled) return null;
    const evaluations = await mapLimit(rows, 4, (row) => (state.cancelled ? null
      : app.api.get(`/v1/admin/evaluations/${row.evaluationId}`)),
    (done, total) => progress(done, total, `Karneler okunuyor… ${done} / ${total}`));
    const className = ctx.classList.find((item) => item.id === classGroupId)?.name || '';
    return reportCardDoc(evaluations.filter(Boolean),
      { title: [period?.name, className, 'karneleri'].filter(Boolean).join(' · ') });
  }

  // ------------------------------------------------------------- çizim
  function paint() {
    if (!period) return;
    const closed = period.status === 'closed';
    const started = (period.progress?.draft || 0) + (period.progress?.published || 0) > 0;
    const actions = [];
    if (period.status === 'open') {
      actions.push(actionButton(app, 'admin_createEvaluation', 'Sıradaki öğrenciyi değerlendir', {
        variant: 'primary', onClick: (event) => nextStudent(event.currentTarget) }));
    }
    actions.push(actionButton(app, 'admin_exportEvaluationReports', 'Gelişim karnesi (PDF)', {
      onClick: () => exportReports(ctx, { periodId, periodName: period.name, classGroupId }) }));
    if (app.can('admin_getEvaluation')) {
      actions.push(documentButtons(app, { title: 'Karneler', label: 'Karneler',
        build: (progress, state) => reportCards(progress, state) }));
    }
    if (!closed) {
      actions.push(actionButton(app, 'admin_updateEvaluationPeriod', 'Düzenle', { onClick: () => edit() }));
      actions.push(actionButton(app, 'admin_closeEvaluationPeriod', 'Dönemi kapat', {
        onClick: (event) => close(event.currentTarget) }));
    }
    actions.push(started
      ? blockedButton('Sil', 'Değerlendirmesi olan dönem silinemez; kapatabilirsiniz.', { variant: 'danger' })
      : actionButton(app, 'admin_deleteEvaluationPeriod', 'Sil', { variant: 'danger',
        onClick: (event) => remove(event.currentTarget) }));

    const title = h('div', 'ak-head');
    const text = h('div', 'ak-head-text');
    const line = h('div', 'ak-inline');
    line.append(h('h2', 'ak-title', period.name), statusBadge(PERIOD_STATUS, period.status));
    text.append(line, h('div', 'ak-sub', `${period.template?.name || '—'} · v${period.template?.version ?? '—'} · `
      + `${fmtDate(period.startDate)} – ${fmtDate(period.dueDate)}`));
    const tools = h('div', 'ak-tools');
    tools.append(...actions);
    title.append(text, tools);
    const top = h('div', 'ak-inline');
    top.append(back);
    headSlot.replaceChildren(top, title);
    paintSummary();
  }

  function paintSummary() {
    const progress = period.progress || {};
    const nodes = [];
    if (period.status === 'closed') {
      nodes.push(alertBox(`Dönem ${fmtDateTime(period.closedAt)} tarihinde kapandı: yeni değerlendirme açılamaz; `
        + 'taslaklar düzenlenip yayımlanabilir.', 'info'));
    } else if (period.status === 'scheduled') {
      nodes.push(alertBox(`Dönem ${fmtDate(period.startDate)} günü kendiliğinden açılır; o güne kadar `
        + 'değerlendirme açılamaz.', 'info'));
    } else if (period.dueDate < todayIst() && progress.notStarted) {
      nodes.push(alertBox(`Son tarih geçti; ${num(progress.notStarted)} öğrenci henüz değerlendirilmedi.`, 'warn'));
    }
    nodes.push(kpiRow([
      { label: classGroupId ? 'Öğrenci (sınıf)' : 'Öğrenci', value: num(progress.studentCount || 0) },
      { label: 'Yayımlandı', value: num(progress.published || 0), tone: 'good' },
      { label: 'Taslak', value: num(progress.draft || 0), tone: progress.draft ? 'warn' : '' },
      { label: 'Başlanmadı', value: num(progress.notStarted || 0) },
      { label: 'Tamamlama', value: percent(progress.percent || 0, 0) },
    ]));
    nodes.push(card('', kv([
      ['Şablon', `${period.template?.name || '—'} · sürüm ${period.template?.version ?? '—'}`],
      ['Hedef', targetText(period.target, ctx.names, { full: true })],
      ['Tarih', `${fmtDate(period.startDate)} – ${fmtDate(period.dueDate)}`],
      ['Varsayılan paylaşım', shareText(period.shareDefaults)],
      ['Personel hatırlatması', period.remindStaff ? 'Açık (son tarihe doğru hatırlatılır)' : 'Kapalı'],
      ['Oluşturuldu', fmtDateTime(period.createdAt)],
    ])));
    summarySlot.replaceChildren(...nodes);
    chips.counts({ not_started: progress.notStarted ?? '', draft: progress.draft ?? '',
      published: progress.published ?? '' });
  }

  async function refresh() {
    const mine = ++token;
    try {
      const next = await app.api.get(`/v1/admin/evaluation-periods/${periodId}`,
        { classGroupId: classGroupId || undefined });
      if (mine !== token) return;
      period = next;
      paint();
      if (!classGroupId) onChanged?.(next);
    } catch (failure) {
      if (mine !== token) return;
      if (!period) {
        headSlot.replaceChildren(back);
        summarySlot.replaceChildren(alertBox(problem(failure, 'Dönem açılamadı.'), 'bad'),
          button('Tekrar dene', { variant: 'ghost', onClick: () => refresh() }));
      } else {
        app.toast(problem(failure, 'Dönem özeti tazelenemedi.'), 'bad');
      }
    }
  }

  async function edit() {
    const saved = await periodDialog(ctx, period);
    if (!saved) return;
    app.toast('Dönem güncellendi.', 'good');
    ctx.periods(true).catch(() => {});
    ctx.emit({ type: 'period', kind: 'updated', period: saved });
    if (classGroupId) refresh(); else { period = saved; paint(); onChanged?.(saved); }
    students.reload();
  }

  async function close(control) {
    const notStarted = period.progress?.notStarted || 0;
    const ok = await confirmAction(app, { title: `“${period.name}” kapatılsın mı?`,
      description: `${notStarted ? `${num(notStarted)} öğrenci henüz değerlendirilmedi. ` : ''}Kapanan dönemde `
        + 'yeni değerlendirme açılamaz; taslaklar taslak kalır ve yayımlanabilir.',
      confirmLabel: 'Dönemi kapat', danger: Boolean(notStarted) });
    if (!ok) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/evaluation-periods/${periodId}/close`),
      { success: 'Dönem kapatıldı.', button: control });
    if (!next) return;
    ctx.periods(true).catch(() => {});
    ctx.emit({ type: 'period', kind: 'closed', period: next });
    if (classGroupId) refresh(); else { period = next; paint(); onChanged?.(next); }
    students.reload();
  }

  async function remove(control) {
    const ok = await confirmAction(app, { title: `“${period.name}” silinsin mi?`,
      description: 'Dönemde hiç değerlendirme yok; dönem kalıcı olarak silinir.', confirmLabel: 'Sil', danger: true });
    if (!ok) return;
    const done = await runAction(app, async () => {
      await app.api.del(`/v1/admin/evaluation-periods/${periodId}`);
      return true;
    }, { success: 'Dönem silindi.', button: control });
    if (!done) return;
    ctx.periods(true).catch(() => {});
    ctx.emit({ type: 'period', kind: 'deleted', removedId: periodId });
    onDeleted?.(periodId);
  }

  const listCard = h('div', 'ak-stack');
  listCard.append(toolbar(chips.node, h('span', 'kit-spacer'), classSelect.node), students.node);
  node.append(headSlot, summarySlot, listCard);
  if (period) paint();
  else headSlot.append(back);
  refresh();
  students.reload();
  // Sınıf adları geç gelirse seçici ve hedef metni tamamlanır.
  ctx.loadNames().then(() => {
    classSelect.options([{ value: '', label: 'Bütün sınıflar' },
      ...ctx.classList.map((item) => ({ value: item.id, label: item.name }))]);
    if (period) paintSummary();
  }).catch(() => {});

  return {
    node,
    periodId,
    destroy() { token += 1; offEvents(); refreshSoon.cancel?.(); students.destroy(); },
  };
}

// -------------------------------------------------------------- pencere

/** Dönem aç / düzenle. Kaydedilen dönemi döndürür. */
async function periodDialog(ctx, period) {
  const { app } = ctx;
  const editing = Boolean(period);
  const started = editing && (period.progress?.draft || 0) + (period.progress?.published || 0) > 0;
  const [templates] = await Promise.all([ctx.templates().catch(() => []), ctx.loadNames().catch(() => null)]);
  const templateOptions = templates.map((item) => ({ value: item.id, label: `${item.name} · v${item.version}` }));
  if (editing && period.template && !templateOptions.some((option) => option.value === period.template.id)) {
    templateOptions.unshift({ value: period.template.id, label: `${period.template.name} (arşivde)` });
  }
  if (!templateOptions.length) {
    app.toast('Önce etkin bir değerlendirme şablonu oluşturun (Şablonlar sekmesi).', 'warn');
    ctx.goTab?.('templates');
    return null;
  }
  const today = todayIst();
  return formDialog(app, {
    title: editing ? `Dönemi düzenle: ${period.name}` : 'Değerlendirme dönemi aç',
    description: editing
      ? (started ? 'Değerlendirme başladığı için şablon değiştirilemez; hedef genişletilebilir.'
        : 'Değişiklikler hemen geçerli olur.')
      : 'Hedef boş bırakılırsa kurumun bütün aktif öğrencileri dahil olur. Başlangıç günü gelince dönem açılır.',
    wide: true,
    value: {
      name: period?.name || '',
      templateId: period?.template?.id || templateOptions[0].value,
      startDate: period?.startDate || today,
      dueDate: period?.dueDate || addDays(today, 14),
      shareStudent: period ? Boolean(period.shareDefaults?.student) : true,
      shareGuardians: period ? Boolean(period.shareDefaults?.guardians) : true,
      remindStaff: period ? Boolean(period.remindStaff) : true,
      target: {
        classGroupIds: [...(period?.target?.classGroupIds || [])],
        studentGroupIds: [...(period?.target?.studentGroupIds || [])],
        studentIds: [...(period?.target?.studentIds || [])],
      },
    },
    fields: [
      { key: 'name', label: 'Dönem adı', required: true, maxLength: 80, placeholder: 'ör. Ekim değerlendirmesi',
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter.' : null) },
      { key: 'templateId', label: 'Şablon', type: 'select', required: true, options: templateOptions,
        readOnly: started, hint: started ? 'Değerlendirme başladı; şablon değişmez.' : '' },
      { key: 'startDate', label: 'Başlangıç', type: 'date', required: true },
      { key: 'dueDate', label: 'Son tarih', type: 'date', required: true,
        validate: (value, draft) => (value && draft.startDate && value < draft.startDate
          ? 'Son tarih başlangıçtan önce olamaz.' : null) },
      { key: 'shareStudent', label: 'Yayımlanınca öğrenciyle paylaş (varsayılan)', type: 'checkbox' },
      { key: 'shareGuardians', label: 'Yayımlanınca velilerle paylaş (varsayılan)', type: 'checkbox' },
      { key: 'remindStaff', label: 'Son tarihe doğru personele hatırlat', type: 'checkbox', wide: true },
      { key: 'target', label: 'Hedef', type: 'custom',
        render: (draft, set) => targetPicker(ctx, draft.target, set, { lockedStudents: editing }) },
    ],
    submitLabel: editing ? 'Kaydet' : 'Dönemi aç',
    onSubmit: (draft, { key }) => {
      const body = {
        name: String(draft.name || '').trim(),
        templateId: draft.templateId,
        target: draft.target,
        startDate: draft.startDate,
        dueDate: draft.dueDate,
        shareDefaults: { student: Boolean(draft.shareStudent), guardians: Boolean(draft.shareGuardians) },
        remindStaff: Boolean(draft.remindStaff),
      };
      return editing
        ? app.api.put(`/v1/admin/evaluation-periods/${period.id}`, body)
        : app.api.post('/v1/admin/evaluation-periods', body, { key });
    },
  });
}

/**
 * Hedef seçici: sınıflar + çalışma grupları (çip) + tek tek öğrenciler.
 * Düzenlemede önceden seçili öğrencilerin adı yükte yoktur; sayısı yazılır.
 */
function targetPicker(ctx, target, set, { lockedStudents = false } = {}) {
  const node = h('div', 'ak-ev-target');
  const value = { classGroupIds: [...target.classGroupIds], studentGroupIds: [...target.studentGroupIds],
    studentIds: [...target.studentIds] };
  const known = new Map();            // seçilen öğrenci → ad
  const preset = lockedStudents ? new Set(value.studentIds) : new Set();
  const commit = () => { set({ ...value }); paintSummary(); };
  const summary = h('div', 'ak-muted ak-ev-target-summary');

  const classOptions = ctx.classList.map((item) => ({ value: item.id, label: item.name }));
  const groupOptions = ctx.groupList.map((item) => ({ value: item.id, label: item.name }));
  if (classOptions.length) {
    node.append(h('small', 'ak-ev-target-label', 'Sınıflar'),
      multiChips(classOptions, value.classGroupIds, (list) => { value.classGroupIds = list; commit(); }).node);
  }
  if (groupOptions.length) {
    node.append(h('small', 'ak-ev-target-label', 'Çalışma grupları'),
      multiChips(groupOptions, value.studentGroupIds, (list) => { value.studentGroupIds = list; commit(); }).node);
  }
  node.append(h('small', 'ak-ev-target-label', 'Tek tek öğrenciler'));
  const chosen = h('div', 'ak-chips');
  const paintStudents = () => {
    chosen.replaceChildren();
    const presetCount = value.studentIds.filter((id) => preset.has(id)).length;
    if (presetCount) {
      chosen.append(removableChip(`${num(presetCount)} önceden seçilmiş öğrenci`, () => {
        value.studentIds = value.studentIds.filter((id) => !preset.has(id));
        paintStudents();
        commit();
      }, 'Önceden seçilenleri kaldır'));
    }
    for (const id of value.studentIds.filter((item) => !preset.has(item))) {
      chosen.append(removableChip(known.get(id) || 'Öğrenci', () => {
        value.studentIds = value.studentIds.filter((item) => item !== id);
        paintStudents();
        commit();
      }));
    }
  };
  if (ctx.app.can('admin_listStudents')) {
    const picker = studentPicker(ctx.app, { placeholder: 'Öğrenci ekle (ad, okul no)', onPick: (student) => {
      if (!value.studentIds.includes(student.id)) {
        value.studentIds.push(student.id);
        known.set(student.id, student.displayName || student.fullName || 'Öğrenci');
        paintStudents();
        commit();
      }
    } });
    node.append(picker.node);
  }
  node.append(chosen, summary);
  function paintSummary() {
    summary.textContent = `Kapsam: ${targetText(value, ctx.names)}`;
  }
  paintStudents();
  paintSummary();
  return node;
}
