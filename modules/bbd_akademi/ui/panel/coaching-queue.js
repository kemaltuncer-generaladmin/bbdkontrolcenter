// BBD Akademi — Koçluk → Kuyruk: koç iş kuyruğu + kontrol kuyruğu (F-K12, F-K13, F-K21).
//
// İş kuyruğu (`admin_getCoachQueue`): kontrol bekleyen teslimler, cevapsız
// sorular (SLA'yı aşan kırmızı), erteleme talepleri, yayımlanmamış haftalar,
// bugünkü görüşmeler, yeni erken uyarılar, doldurulmamış check-in'ler — tek
// liste, aciliyet sırası; satır ilgili ekrana götürür.
//
// Kontrol kuyruğu (`admin_listCoachReviews`): teslim edilen koç görevleri
// (eski önce). Ayrıntıda D/Y/B, süre, zorluk, "anlamadığım yer var", fotoğraf
// kanıtı ve video izlenme (`students_getTaskVideo`); karar onayla / tekrar iste
// (gerekçe + yeni gün; görev yeniden açılır) / yorum. Toplu onay seçilenlere ya
// da süzgece (`admin_bulkApproveCoachTasks`, en çok 200; öğrenci başına tek
// özet bildirim).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, chipRow, hintBox } from '../../ui-kit/layout.js';
import {
  actionButton, fmtDate, fmtDateTime, formDialog, kv, openDrawer, optionsOf, pagedList, runAction,
  selectBox, statusBadge, toolbar, todayIst,
} from './common.js';
import { newKey } from './api.js';
import {
  PRIORITY, QUEUE_KINDS, REACTIONS, REVIEW_STATUS, TASK_TYPES, bulkApproveBody, clockText, minutesText, pct,
  resultText, reviewBody, taskDetail,
} from './coaching-logic.js';
import { coachOptions, openFile, setEnabled, studentCell } from './coaching-shared.js';

const VIEWS = [{ key: 'reviews', label: 'Teslim kontrolü' }, { key: 'queue', label: 'İş kuyruğu' }];

export function tab(app, ctx) {
  const node = h('div', 'ak-stack');
  const canReviews = app.can('admin_listCoachReviews');
  const canQueue = app.can('admin_getCoachQueue');
  let view = canReviews ? 'reviews' : 'queue';
  const views = VIEWS.filter((item) => (item.key === 'reviews' ? canReviews : canQueue));
  const chips = chipRow(views, view, (key) => {
    view = key || view;
    chips.set(view);
    paint();
  });
  const coachSlot = h('div');
  let coachId = '';
  let coachBox = null;
  node.append(toolbar(chips.node, coachSlot));
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachBox = selectBox(options, coachId, (value) => { coachId = value; reviews?.reload(); queue?.reload(); },
      { label: 'Koç' });
    coachSlot.append(coachBox.node);
  });

  const holder = h('div');
  node.append(holder);
  let reviews = null;
  let queue = null;

  function paint() {
    if (view === 'reviews') {
      reviews = reviews || reviewsView(app, () => coachId);
      holder.replaceChildren(reviews.node);
      reviews.start();
    } else {
      queue = queue || queueView(app, ctx, () => coachId, (studentId) => {
        view = 'reviews';
        chips.set('reviews');
        reviews = reviews || reviewsView(app, () => coachId);
        holder.replaceChildren(reviews.node);
        reviews.filterStudent(studentId);
      });
      holder.replaceChildren(queue.node);
      queue.start();
    }
  }

  let started = false;
  return {
    node,
    shown(payload) {
      // Koçlar sekmesinden "Koçun kuyruğu": koç süzgeci seçili açılır.
      if (payload?.coachId && payload.coachId !== coachId) {
        coachId = payload.coachId;
        coachBox?.set?.(coachId);
        if (started) { reviews?.reload(); queue?.reload(); }
      }
      if (!started) { started = true; paint(); }
    },
    destroy() { reviews?.destroy(); queue?.destroy(); },
  };
}

// ---------------------------------------------------------------- iş kuyruğu

function queueView(app, ctx, coachOf, toReviews) {
  const node = h('div', 'ak-stack');
  let kind = '';
  const kindBox = selectBox(optionsOf(QUEUE_KINDS, { empty: 'Bütün işler' }), '', (value) => { kind = value; list.reload(); },
    { label: 'İş türü' });
  node.append(toolbar(kindBox.node), hintBox('Aciliyet sırası: süresi aşılan soru ve geciken kontrol kırmızı ve en üstte. '
    + 'Satıra tıklayınca işin ekranı açılır.'));

  const go = (row) => {
    const student = row.student;
    switch (row.kind) {
      case 'review': toReviews(student?.id); break;
      case 'question': app.go('questions'); break;
      case 'unpublished_week': ctx.go('week', { student }); break;
      case 'meeting': ctx.go('meetings', { studentId: student?.id }); break;
      case 'checkin_missing': ctx.go('checkins'); break;
      case 'warning': app.go('insights'); break;
      case 'postpone_request': app.go('plan'); break;
      default: ctx.openStudent(student);
    }
  };
  const list = pagedList({
    app,
    path: '/v1/admin/coaching/queue',
    query: () => ({ coachId: coachOf() || undefined, kind: kind || undefined }),
    limit: 50,
    onRow: go,
    columns: [
      { key: 'priority', label: 'Öncelik', width: '110px', cell: (row) => (row.overdue ? badge('Gecikti', 'bad')
        : badge(String(row.priority ?? 0), row.priority >= 70 ? 'warn' : 'dim')) },
      { key: 'kind', label: 'İş', width: '190px', cell: (row) => QUEUE_KINDS[row.kind] || row.kind },
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1fr)', cell: (row) => studentCell(row.student) },
      { key: 'title', label: 'Ayrıntı', width: 'minmax(0, 1.6fr)', cell: (row) => row.title || '—' },
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1fr)', cell: (row) => row.coach?.displayName || '—' },
      { key: 'dueAt', label: 'Son', width: '140px', cell: (row) => (row.dueAt ? fmtDateTime(row.dueAt) : '—') },
    ],
    empty: { title: 'Kuyruk boş', text: 'Bekleyen koçluk işi yok.' },
  });
  node.append(list.node);
  let started = false;
  return { node, start() { if (!started) { started = true; list.reload(); } }, reload: () => started && list.reload(),
    destroy: () => list.destroy() };
}

// ------------------------------------------------------------ kontrol kuyruğu

function reviewsView(app, coachOf) {
  const node = h('div', 'ak-stack');
  const filter = { status: 'pending', minAccuracyPercent: '', photoRequired: false, studentId: '', studentName: '' };
  const statusBox = selectBox(Object.entries(REVIEW_STATUS).filter(([key]) => key !== 'none')
    .map(([value, [label]]) => ({ value, label })), 'pending', (value) => { filter.status = value; reloadAll(); },
  { label: 'Durum' });
  const accuracy = h('input', 'kit-input ak-coach-num');
  accuracy.type = 'text';
  accuracy.inputMode = 'numeric';
  accuracy.placeholder = 'Doğruluk ≥ %';
  accuracy.setAttribute('aria-label', 'En düşük doğruluk yüzdesi');
  accuracy.addEventListener('change', () => {
    const value = Number(accuracy.value);
    filter.minAccuracyPercent = accuracy.value && value >= 0 && value <= 100 ? Math.round(value) : '';
    reloadAll();
  });
  const photoLabel = h('label', 'ak-inline');
  const photo = h('input', 'kit-check');
  photo.type = 'checkbox';
  photo.addEventListener('change', () => { filter.photoRequired = photo.checked; reloadAll(); });
  photoLabel.append(photo, h('span', '', 'Yalnız fotoğraf kanıtlı'));
  const studentNote = h('span', 'ak-muted');
  const clearStudent = button('Öğrenci süzgecini kaldır', { variant: 'ghost', onClick: () => {
    filter.studentId = ''; studentNote.textContent = ''; clearStudent.hidden = true; reloadAll();
  } });
  clearStudent.hidden = true;
  const approveSelected = actionButton(app, 'admin_bulkApproveCoachTasks', 'Seçilenleri onayla', { variant: 'primary',
    onClick: () => bulkApprove({ selected: true }) });
  const approveFiltered = actionButton(app, 'admin_bulkApproveCoachTasks', 'Süzgece uyanları onayla', { variant: 'ghost',
    onClick: () => bulkApprove({ selected: false }) });
  setEnabled(approveSelected, false);
  node.append(toolbar(statusBox.node, accuracy, photoLabel, studentNote, clearStudent),
    toolbar(approveSelected, approveFiltered));

  const query = () => ({ coachId: coachOf() || undefined, status: filter.status,
    minAccuracyPercent: filter.minAccuracyPercent === '' ? undefined : filter.minAccuracyPercent,
    photoRequired: filter.photoRequired || undefined, studentId: filter.studentId || undefined });
  const list = pagedList({
    app,
    path: '/v1/admin/coaching/reviews',
    query,
    limit: 50,
    selectable: true,
    rowKey: (row) => row.task?.id,
    onSelect: (keys) => setEnabled(approveSelected, keys.length > 0),
    onRow: (row) => openReview(app, row, (next) => {
      if (next.remove) list.remove((item) => item.task?.id === row.task?.id);
    }),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1fr)', cell: (row) => studentCell(row.student) },
      { key: 'task', label: 'Görev', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.task?.title || TASK_TYPES[row.task?.type] || 'Görev'), h('small', '', taskDetail(row.task)));
        return cell;
      } },
      { key: 'submittedAt', label: 'Teslim', width: '140px', cell: (row) => fmtDateTime(row.submittedAt) },
      { key: 'result', label: 'Sonuç', width: '150px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', resultText(row)), h('small', '', row.accuracyPercent === null || row.accuracyPercent === undefined
          ? '' : `doğruluk ${pct(row.accuracyPercent)}`));
        return cell;
      } },
      { key: 'minutes', label: 'Süre', width: '120px', cell: (row) => (row.minutes ? `${minutesText(row.minutes)}${row.targetMinutes
        ? ` / ${minutesText(row.targetMinutes)}` : ''}` : '—') },
      { key: 'flags', label: 'İşaret', width: '170px', cell: (row) => {
        const cell = h('span', 'ak-coach-flags');
        if (row.needsHelp) cell.append(badge('Anlamadığım yer var', 'warn'));
        if (row.suggestRedo) cell.append(badge('Tekrar önerilir', 'bad'));
        if (row.photos?.length) cell.append(badge(`${row.photos.length} fotoğraf`, 'info'));
        return cell;
      } },
    ],
    empty: { title: 'Kuyruk boş', text: 'Bu süzgeçteki bütün teslimler kontrol edildi.' },
  });
  node.append(list.node);

  function reloadAll() {
    setEnabled(approveSelected, false);
    list.reload();
  }

  async function bulkApprove({ selected }) {
    const taskIds = selected ? list.table.selection() : [];
    const draft = { reaction: '', comment: '' };
    const filterText = [filter.minAccuracyPercent !== '' ? `doğruluk ≥ %${filter.minAccuracyPercent}` : '',
      filter.photoRequired ? 'fotoğraf kanıtlı' : '', filter.studentId ? 'seçili öğrenci' : '',
      coachOf() ? 'seçili koç' : ''].filter(Boolean).join(' · ') || 'bütün bekleyen teslimler';
    const result = await formDialog(app, {
      title: selected ? `${taskIds.length} teslimi onayla` : 'Süzgece uyanları onayla',
      description: selected ? 'Seçilen teslimler onaylanır; öğrencilere tek özet bildirim gider.'
        : `Süzgeç: ${filterText}. En çok 200 teslim; öğrencilere tek özet bildirim gider.`,
      fields: [
        { key: 'reaction', label: 'Hızlı tepki', type: 'select',
          options: [{ value: '', label: 'Yok' }, ...REACTIONS.map((item) => ({ value: item, label: item }))] },
        { key: 'comment', label: 'Ortak yorum (isteğe bağlı)', type: 'textarea', maxLength: 500, wide: true },
      ],
      value: draft,
      submitLabel: 'Onayla',
      onSubmit: (form, { key }) => {
        const built = bulkApproveBody({ taskIds, reaction: form.reaction, comment: form.comment,
          filter: selected ? null : { minAccuracyPercent: filter.minAccuracyPercent === '' ? null : filter.minAccuracyPercent,
            photoRequired: filter.photoRequired, coachId: coachOf() || null, studentId: filter.studentId || null } });
        if (built.error) throw new Error(built.error);
        return app.api.post('/v1/admin/coaching/reviews/bulk-approve', built.body, { key });
      },
    });
    if (!result) return;
    const skipped = result.skipped?.length || 0;
    app.toast(`${result.approved} teslim onaylandı${skipped ? `, ${skipped} atlandı` : ''}.`, 'good');
    reloadAll();
  }

  let started = false;
  return {
    node,
    start() { if (!started) { started = true; list.reload(); } },
    reload: () => started && reloadAll(),
    filterStudent(studentId) {
      if (!studentId) { this.start(); return; }
      started = true;
      filter.studentId = studentId;
      studentNote.textContent = 'Tek öğrencinin teslimleri';
      clearStudent.hidden = false;
      reloadAll();
    },
    destroy: () => list.destroy(),
  };
}

/** Teslim ayrıntısı + karar. `onDone({remove})`. */
export async function openReview(app, row, onDone) {
  const task = row.task || {};
  const studentId = row.student?.id;
  const view = openDrawer(app, { title: row.student?.displayName || 'Teslim',
    subtitle: task.title || TASK_TYPES[task.type] || '' });
  const clientUuid = (globalThis.crypto?.randomUUID?.() || newKey('rv').replace(/^rv-/, '')).slice(0, 36);
  const parts = [];
  parts.push(kv([
    ['Görev', `${TASK_TYPES[task.type] || task.type || '—'} · ${fmtDate(task.date)}`],
    ['Ayrıntı', taskDetail(task) || '—'],
    ['Öncelik', PRIORITY[task.coachTask?.priority] || 'Normal'],
    ['Talimat', task.coachTask?.instructions || '—'],
    ['Teslim', fmtDateTime(row.submittedAt)],
    ['Sonuç', `${resultText(row)}${row.accuracyPercent === null || row.accuracyPercent === undefined ? ''
      : ` · doğruluk ${pct(row.accuracyPercent)}`}`],
    ['Süre', row.minutes ? `${minutesText(row.minutes)}${row.targetMinutes ? ` (hedef ${minutesText(row.targetMinutes)})` : ''}` : '—'],
    ['Zorluk (öğrenci)', row.difficulty ? `${row.difficulty} / 5` : '—'],
    ['Öğrencinin notu', row.note || '—'],
    ['Durum', statusBadge(REVIEW_STATUS, task.coachTask?.review?.status || 'pending')],
  ]));
  if (row.needsHelp) parts.push(alertBox('Öğrenci "anlamadığım yer var" dedi.', 'warn'));
  if (row.suggestRedo) parts.push(alertBox('Doğruluk başarı ölçütünün altında — tekrar görevi önerilir.', 'warn'));
  if (row.photos?.length) {
    const photos = h('div', 'ak-row-actions');
    row.photos.forEach((file, index) => photos.append(button(`Fotoğraf ${index + 1}`, { variant: 'ghost',
      onClick: () => openFile(app, file, `kanit-${index + 1}.jpg`) })));
    parts.push(h('h4', 'ak-coach-sub', 'Fotoğraf kanıtı'), photos);
  }
  if (row.videos?.length) {
    const videos = h('div', 'ak-stack');
    parts.push(h('h4', 'ak-coach-sub', 'Videolar'), videos);
    for (const item of row.videos) {
      const line = h('div', 'ak-coach-video');
      line.append(h('span', '', item.selfReported ? 'Öğrenci "izledim" dedi' : `İzlenme ${pct(item.measuredPercent)}`));
      videos.append(line);
      if (studentId && app.can('students_getTaskVideo')) {
        app.api.get(`/v1/students/${studentId}/plan/tasks/${task.id}/videos/${item.taskVideoId}`).then((video) => {
          line.replaceChildren(videoSummary(video));
        }).catch(() => {});
      }
    }
  }
  const tools = h('div', 'ak-coach-decide');
  const reaction = selectBox([{ value: '', label: 'Hızlı tepki yok' }, ...REACTIONS.map((item) => ({ value: item, label: item }))],
    '', () => {}, { label: 'Hızlı tepki' });
  const comment = h('textarea', 'kit-textarea');
  comment.maxLength = 1000;
  comment.placeholder = 'Yorum (öğrenci görür)';
  comment.setAttribute('aria-label', 'Yorum');
  const error = h('div', 'ak-coach-error');
  error.setAttribute('role', 'alert');
  const decide = async (decision, extra = {}, control = null) => {
    error.textContent = '';
    const built = reviewBody({ clientUuid, decision, comment: extra.comment ?? comment.value, reaction: reaction.value,
      redoDate: extra.redoDate });
    if (built.error) { error.textContent = built.error; return; }
    const saved = await runAction(app, () => app.api.post(`/v1/admin/coaching/reviews/${task.id}/decision`, built.body),
      { success: decision === 'approve' ? 'Onaylandı; öğrenciye bildirim gitti.' : decision === 'redo'
        ? 'Görev yeniden açıldı; öğrenciye bildirim gitti.' : 'Yorum gönderildi.', button: control });
    if (saved) { view.close(); onDone?.({ remove: true, task: saved }); }
  };
  const approve = actionButton(app, 'admin_reviewCoachTask', 'Onayla', { variant: 'primary',
    onClick: (event) => decide('approve', {}, event.currentTarget) });
  const redo = actionButton(app, 'admin_reviewCoachTask', 'Tekrar iste', { variant: 'ghost', onClick: async () => {
    const form = await formDialog(app, {
      title: 'Tekrar iste',
      description: 'Görev yeni gününde yeniden açılır; gerekçe öğrenciye gider.',
      fields: [
        { key: 'comment', label: 'Gerekçe', type: 'textarea', required: true, wide: true, maxLength: 1000 },
        { key: 'redoDate', label: 'Yeni gün', type: 'date', required: true },
      ],
      value: { comment: comment.value, redoDate: todayIst(1) },
      submitLabel: 'Görevi yeniden aç',
      onSubmit: async (draft) => draft,
    });
    if (form) await decide('redo', form);
  } });
  const note = actionButton(app, 'admin_reviewCoachTask', 'Yorum gönder', { variant: 'ghost',
    onClick: (event) => decide('comment', {}, event.currentTarget) });
  tools.append(reaction.node, comment, error, toolbar(approve, redo, note));
  parts.push(h('h4', 'ak-coach-sub', 'Karar'), tools);
  view.body.replaceChildren(...parts);
}

/** `TaskVideoView` → kısa izlenme özeti. */
export function videoSummary(video) {
  const box = h('div', 'ak-cell-stack');
  const progress = video?.progress;
  const title = video?.video?.title || 'Video';
  const state = !progress ? 'Henüz izlenmedi'
    : progress.completed ? (progress.completionSource === 'self_report' ? 'Tamamlandı (öğrenci beyanı)' : 'Tamamlandı')
      : `İzlenen ${clockText(progress.watchedSeconds)}${progress.segmentSeconds ? ` / ${clockText(progress.segmentSeconds)}` : ''}`;
  box.append(h('span', '', title), h('small', '', [state,
    progress?.measuredPercent !== null && progress?.measuredPercent !== undefined ? pct(progress.measuredPercent) : '',
    progress?.checkpointsAnswered ? `durak ${progress.checkpointsCorrect}/${progress.checkpointsAnswered} doğru` : '',
    progress?.note ? `"${progress.note}"` : ''].filter(Boolean).join(' · ')));
  return box;
}

