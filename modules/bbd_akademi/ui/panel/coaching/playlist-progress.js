// BBD Akademi — liste atamaları ve liste ilerleme raporu (Akademi README §14 madde 124; F-K9, F-K10, F-K4).
//
// · Liste (`admin_listPlaylistAssignments`): atanan oynatma listeleri — kip, video / öğrenci sayısı, günler, sırayla.
// · Ayrıntı (`admin_getPlaylistAssignmentProgress`; yetki yoksa `admin_getPlaylistAssignment`): özet (öğrenci,
//   tamamlayan, ortalama %), öğrenci bazında "4/12 izlendi · 1 sa 40 dk kaldı" + sıradaki video, video bazında
//   tamamlayan / atanan ve ortalama ölçülen izleme; listeden çıkarılan video "YouTube'dan kaldırıldı" rozetiyle.
// · Öğrenci satırı → öğrencinin gördüğü liste görevi (`students_getPlaylistAssignment`): videolar sırayla, kilitli
//   adımlar, video başına ilerleme (ölçülen % / beyan), "Sıradaki video".

import { h } from '../../../ui-kit/kit.js';
import { badge, kpiRow, skeletonRows } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { errorBox, fmtDateTime, kv, num, openDrawer, pagedList, searchBox, toolbar } from '../common.js';
import { playlistStyles } from './playlist.js';
import {
  ASSIGN_MODES, assignmentLine, cardLine, clock, dateSpan, durationText, percentText, progressText,
} from './playlist-logic.js';

const ASSIGNMENTS = '/v1/admin/playlist-assignments';

/** Liste atamaları görünümü (Kütüphane sekmesinin "Liste atamaları" çipi). Dönüş {node, start(), destroy()}. */
export function assignmentsView(app) {
  playlistStyles();
  const node = h('div', 'ak-stack');
  const filter = { q: '' };
  const search = searchBox({ placeholder: 'Liste adı ara', label: 'Liste atamalarında ara',
    onSearch: (text) => { filter.q = text; list.reload(); } });
  node.append(toolbar(search.node));
  const list = pagedList({
    app,
    path: ASSIGNMENTS,
    query: () => ({}),
    limit: 50,
    map: (rows) => {
      const q = filter.q.toLocaleLowerCase('tr-TR');
      return q ? rows.filter((row) => `${row.title} ${row.playlist?.title || ''}`.toLocaleLowerCase('tr-TR').includes(q)) : rows;
    },
    onRow: (row) => openAssignment(app, row),
    columns: [
      { key: 'title', label: 'Liste', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.title), h('small', '', [row.playlist?.channelTitle, assignmentLine(row)]
          .filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'mode', label: 'Biçim', width: '130px', cell: (row) => ASSIGN_MODES[row.mode] || row.mode },
      { key: 'dates', label: 'Günler', width: '170px', cell: (row) => dateSpan(row.firstDate, row.lastDate) || '—' },
      { key: 'by', label: 'Atayan', width: '160px', cell: (row) => row.createdByName || '—' },
      { key: 'createdAt', label: 'Atandı', width: '150px', cell: (row) => fmtDateTime(row.createdAt) },
    ],
    empty: { title: 'Henüz liste atanmadı', text: 'Kütüphanede ya da video havuzunda bir oynatma listesini açıp '
      + '“Öğrencilere ata” deyin.' },
  });
  node.append(list.node);
  let started = false;
  return { node, start() { if (!started) { started = true; list.reload(); } }, reload: () => started && list.reload(),
    destroy() { list.destroy(); search.destroy(); } };
}

/** Atama ayrıntısı + ilerleme raporu çekmecesi. */
export async function openAssignment(app, row) {
  playlistStyles();
  const view = openDrawer(app, { title: row.title || 'Liste ataması', subtitle: 'Liste ilerlemesi' });
  view.body.replaceChildren(skeletonRows(6, 3));
  try {
    if (!app.can('admin_getPlaylistAssignmentProgress')) {
      const assignment = await app.api.get(`/v1/admin/playlist-assignments/${row.id}`);
      view.body.replaceChildren(assignmentFacts(assignment));
      return;
    }
    const data = await app.api.get(`/v1/admin/playlist-assignments/${row.id}/progress`);
    view.body.replaceChildren(...progressNodes(app, data));
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => { view.close?.(); openAssignment(app, row); }));
  }
}

function assignmentFacts(assignment) {
  return kv([
    ['Liste', [assignment.playlist?.title, assignment.playlist?.channelTitle].filter(Boolean).join(' · ')],
    ['Biçim', `${ASSIGN_MODES[assignment.mode] || assignment.mode}${assignment.requireOrder ? ' · sırayla izle' : ''}`],
    ['Videolar', `${num(assignment.videoCount)} video`],
    ['Öğrenciler', `${num(assignment.studentCount)} öğrenci · ${num(assignment.taskCount)} görev`],
    assignment.skippedDuplicateCount ? ['Atlanan', `${num(assignment.skippedDuplicateCount)} yinelenen (öğrenci × video)`] : null,
    ['Günler', dateSpan(assignment.firstDate, assignment.lastDate)],
    ['Son tarih', assignment.dueDate ? dateSpan(assignment.dueDate) : '—'],
    ['Atayan', `${assignment.createdByName || '—'} · ${fmtDateTime(assignment.createdAt)}`],
  ]);
}

/** İlerleme raporu düğümleri (test de çağırır). */
export function progressNodes(app, data) {
  const assignment = data.assignment || {};
  const out = [kpiRow([
    { label: 'Öğrenci', value: num(assignment.studentCount ?? data.students?.length ?? 0) },
    { label: 'Bitiren', value: num(data.completedStudentCount ?? 0), tone: data.completedStudentCount ? 'good' : '' },
    { label: 'Ortalama', value: percentText(data.avgPercent) },
    { label: 'Video', value: num(assignment.videoCount ?? data.videos?.length ?? 0) },
  ]), assignmentFacts(assignment)];

  out.push(h('h4', 'ak-coach-sub', 'Öğrenciler (en az ilerleyen önce)'));
  const students = dataTable({
    columns: [
      { key: 'name', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (item) => item.student?.displayName || '—' },
      { key: 'progress', label: 'İlerleme', width: 'minmax(0, 1.6fr)', cell: (item) => progressText(item.completedCount,
        item.videoCount, item.remainingSeconds) },
      { key: 'percent', label: '%', align: 'num', width: '70px', cell: (item) => percentText(item.percent) },
      { key: 'next', label: 'Sıradaki', width: '90px', cell: (item) => (item.nextPosition ? `${item.nextPosition}. video` : 'Bitti') },
      { key: 'last', label: 'Son etkinlik', width: '140px', cell: (item) => (item.lastActivityAt
        ? fmtDateTime(item.lastActivityAt) : 'Henüz yok') },
    ],
    rows: data.students || [],
    rowKey: (item) => item.student?.id || '',
    onRow: app.can('students_getPlaylistAssignment') ? (item) => openStudentView(app, item.student, assignment) : undefined,
    empty: h('p', 'ak-muted', 'Kapsamınızda öğrenci yok.'),
  });
  out.push(students.node);

  out.push(h('h4', 'ak-coach-sub', 'Videolar'));
  const videos = dataTable({
    columns: [
      { key: 'title', label: 'Video', width: 'minmax(0, 2fr)', cell: (item) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', `${item.position}. ${item.title}`));
        if (item.removedFromPlaylist) cell.append(badge('YouTube\'dan kaldırıldı', 'warn'));
        return cell;
      } },
      { key: 'duration', label: 'Süre', width: '80px', cell: (item) => clock(item.durationSeconds) },
      { key: 'done', label: 'Tamamlayan', width: '120px', cell: (item) => `${num(item.completedCount)} / ${num(item.assignedCount)}` },
      { key: 'avg', label: 'Ort. izleme', width: '100px', align: 'num', cell: (item) => percentText(item.avgMeasuredPercent) },
    ],
    rows: data.videos || [],
    rowKey: (item) => item.videoId,
    empty: h('p', 'ak-muted', 'Video yok.'),
  });
  out.push(videos.node);
  return out;
}

/** Öğrencinin gördüğü liste görevi (koç gözüyle): videolar sırayla, kilit, ilerleme, sıradaki. */
export async function openStudentView(app, student, assignment) {
  const view = openDrawer(app, { title: student?.displayName || 'Öğrenci', subtitle: assignment?.title || 'Liste görevi' });
  view.body.replaceChildren(skeletonRows(6, 2));
  try {
    const data = await app.api.get(`/v1/students/${student.id}/playlist-assignments/${assignment.id}`);
    view.body.replaceChildren(...studentNodes(data));
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure));
  }
}

/** Öğrenci görünümü düğümleri (test de çağırır). */
export function studentNodes(data) {
  const out = [h('b', 'ak-pl-card', cardLine(data))];
  if (data.dueDate) out.push(h('small', 'ak-muted', `Son tarih: ${dateSpan(data.dueDate)}`));
  if (data.requireOrder) out.push(h('small', 'ak-muted', 'Sırayla izle: önceki video tamamlanmadan sonraki açılmaz.'));
  const next = data.nextItem ? (data.items || []).find((item) => item.taskVideo?.id === data.nextItem.taskVideoId) : null;
  if (next) out.push(badge(`Sıradaki video: ${next.position}. ${next.taskVideo?.video?.title || ''}`.trim(), 'info'));
  const list = h('div', 'ak-pl-rows');
  list.setAttribute('role', 'list');
  for (const item of data.items || []) {
    const progress = item.taskVideo?.progress;
    const row = h('div', `ak-pl-row${item.locked ? ' locked' : ''}`);
    row.setAttribute('role', 'listitem');
    const main = h('div', 'ak-pl-main');
    main.append(h('span', 'ak-pl-title', `${item.position}. ${item.taskVideo?.video?.title || 'Video'}`),
      h('small', 'ak-muted', [dateSpan(item.date), item.taskVideo?.video?.durationSeconds
        ? durationText(item.taskVideo.video.durationSeconds) : ''].filter(Boolean).join(' · ')));
    row.append(main);
    if (item.taskVideo?.removedFromPlaylist) row.append(badge('YouTube\'dan kaldırıldı', 'warn'));
    if (progress?.completed) {
      row.append(badge(progress.completionSource === 'self_report' ? 'Beyan: izledim'
        : `Tamamlandı${progress.measuredPercent !== null && progress.measuredPercent !== undefined
          ? ` · ölçüldü ${percentText(progress.measuredPercent)}` : ''}`, 'good'));
    } else if (item.locked) {
      row.append(badge('Kilitli', 'dim'));
    } else if (progress?.measuredPercent) {
      row.append(badge(`İzleniyor · ${percentText(progress.measuredPercent)}`, 'info'));
    } else {
      row.append(badge('Başlanmadı', 'dim'));
    }
    list.append(row);
  }
  out.push(list);
  return out;
}
