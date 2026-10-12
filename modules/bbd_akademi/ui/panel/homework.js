// BBD Akademi — Ödevler (F-A9).
//
// Ödev = hedef (sınıf · çalışma grubu · tek tek öğrenci; üçü birlikte de
// olabilir, Akademi birleştirir) + ders + isteğe bağlı müfredat konuları +
// isteğe bağlı kaynak (hazır kütüphane ya da kuruma özel kaynak; bölümler ve
// sayfa aralığı) + son teslim (Europe/Istanbul) + açıklama. Teslim tablosu ve
// "Teslim etmeyenlere hatırlat" ayrıntı çekmecesindedir.
//
// HIZ: liste imleçlidir; teslim oranı liste yükündeki `stats`tan, hedef adları
// önbellekli sınıf / grup listelerinden gelir (satır başına istek yok). Konu
// ve kaynak seçicileri program / kaynak ayrıntısını yalnız seçildiğinde ve
// önbellekten (`getCached`) çeker.
//
// Sözleşmede ödev listesi için ders ya da metin araması yoktur; süzgeçler
// sınıf, öğrenci ve durumdur (süresi dolmamış / geçmiş). Teslim fotoğrafları
// 5 dakikalık imzalı bağlantıdır: süresi dolmuşsa teslim listesi yeniden
// okunur, sonra köprüden indirilir.

import { blockedButton, button, clip, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, kpiRow, skeletonRows, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  actionButton, addDays, clockInput, confirmAction, dateInput, empty, errorBox, fmtDate,
  fmtDateTime, formDialog, gradeText, istanbulStamp, kv, loader, multiChips, num, openDrawer,
  pagedList, percent, problem, runAction, searchBox, sectionHead, selectBox, statusBadge,
  studentPicker, todayIst, toolbar, TRACKS, TZ,
} from './common.js';

const HOMEWORK = '/v1/admin/homework';
const PROGRAMS = '/v1/admin/curricula/programs';

const SUBMISSION = { done: ['Teslim etti', 'good'], late: ['Geç teslim', 'warn'],
  overdue: ['Teslim etmedi', 'bad'], pending: ['Bekliyor', 'info'] };
const SUBMISSION_ORDER = { overdue: 0, pending: 1, late: 2, done: 3 };
const RESOURCE_KINDS = { topic_book: 'Konu anlatımlı', question_bank: 'Soru bankası', fascicle: 'Fasikül',
  practice_exam: 'Deneme', other: 'Diğer' };
const SECTION_KINDS = { test: 'Test', chapter: 'Bölüm', unit_test: 'Ünite testi', review: 'Tekrar',
  other: 'Diğer' };
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

// ---------------------------------------------------------------- yardımcılar

function submissionState(row) {
  if (row.status === 'completed') return row.completion?.late ? 'late' : 'done';
  return row.status === 'overdue' ? 'overdue' : 'pending';
}

/** Zaman damgası → İstanbul'da {day: 'YYYY-MM-DD', clock: 'HH:MM'}. */
function istanbulParts(stamp) {
  const date = new Date(String(stamp || ''));
  if (Number.isNaN(date.getTime())) return { day: '', clock: '' };
  return {
    day: date.toLocaleDateString('sv-SE', { timeZone: TZ }),
    clock: date.toLocaleTimeString('tr-TR', { timeZone: TZ, hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23' }),
  };
}

/** "TYT · 12. sınıf" — konu seçicideki program etiketi (Müfredat ekranındakiyle aynı dil). */
function programLevel(program) {
  const parts = [];
  if (program?.exam) parts.push(program.exam);
  if (program?.grade !== null && program?.grade !== undefined && program?.grade !== '') {
    parts.push(gradeText(program.grade));
  }
  if (program?.tracks?.length) parts.push(program.tracks.map((track) => TRACKS[track] || track).join(' / '));
  return parts.join(' · ') || 'Genel';
}

function isPast(stamp) {
  const time = new Date(String(stamp || '')).getTime();
  return !Number.isNaN(time) && time < Date.now();
}

function stack(main, sub) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', main || '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
}

function removableChip(text, onRemove, title = 'Kaldır') {
  const chip = h('button', 'kit-chip on', `${text} ✕`);
  chip.type = 'button';
  chip.title = title;
  chip.addEventListener('click', onRemove);
  return chip;
}

function topicsText(topics, max = 3) {
  const list = (topics || []).map((topic) => topic.name).filter(Boolean);
  if (!list.length) return '';
  return list.length > max ? `${list.slice(0, max).join(', ')} +${num(list.length - max)}` : list.join(', ');
}

/**
 * Hedef özeti. `names` önbellekli sınıf / grup adlarıdır; öğrenci adı liste
 * yükünde yoktur, sayısı yazılır (satır başına istek atılmaz).
 */
function targetText(target, names, { full = false } = {}) {
  const classes = target?.classGroupIds || [];
  const groups = target?.studentGroupIds || [];
  const students = target?.studentIds || [];
  const parts = [];
  if (classes.length) {
    parts.push(full || classes.length <= 3
      ? classes.map((id) => names.classes.get(id) || 'Sınıf').join(', ') : `${num(classes.length)} sınıf`);
  }
  if (groups.length) {
    parts.push(full || groups.length <= 2
      ? groups.map((id) => names.groups.get(id) || 'Grup').join(', ') : `${num(groups.length)} grup`);
  }
  if (students.length) parts.push(`${num(students.length)} öğrenci`);
  return parts.join(' · ') || '—';
}

function dueCell(dueAt) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', fmtDateTime(dueAt)));
  if (isPast(dueAt)) cell.append(h('small', '', 'Süresi geçti'));
  return cell;
}

function ratioCell(stats) {
  const assigned = stats?.assignedCount || 0;
  const done = stats?.completedCount || 0;
  const share = assigned ? Math.min(1, done / assigned) : 0;
  const cell = h('div', 'ak-hw-ratio');
  const line = h('div', 'ak-hw-ratio-line');
  line.append(h('span', 'ak-num', assigned ? `${num(done)}/${num(assigned)}` : '—'));
  if (assigned) line.append(h('span', 'ak-muted', percent(share * 100, 0)));
  if (stats?.overdueCount) line.append(badge(`${num(stats.overdueCount)} gecikmiş`, 'bad'));
  const bar = h('span', 'ak-hw-bar');
  const fill = h('span', 'ak-hw-fill');
  fill.style.width = `${Math.round(share * 100)}%`;
  bar.append(fill);
  cell.append(line, bar);
  return cell;
}

// ------------------------------------------------------------------ bölüm

export function section(app) {
  loadStyles(import.meta.url, 'homework.css');
  const node = h('div', 'ak-stack ak-hw');
  const names = { classes: new Map(), groups: new Map() };
  const filters = { state: 'active', classGroupId: '', studentId: '', studentLabel: '' };
  let started = false;

  const add = actionButton(app, 'admin_createHomework', 'Yeni ödev', { variant: 'primary',
    onClick: () => editHomework(app, null, { onSaved: (saved) => {
      list.reload();
      if (saved?.id) openDetail(saved.id);
    } }) });
  node.append(sectionHead('Ödevler', {
    subtitle: 'Sınıfa, çalışma grubuna ya da öğrenciye ödev; teslim durumu ve hatırlatma.',
    actions: [add],
  }));

  const chips = chipRow([
    { key: 'active', label: 'Süresi dolmamış' },
    { key: 'past', label: 'Süresi geçmiş' },
  ], filters.state, (key) => { filters.state = key || ''; list.reload(); });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value;
    list.reload();
  }, { label: 'Sınıf' });
  const studentSlot = h('span', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz',
    onPick: (student) => setStudent(student.id, student.displayName || '') });
  node.append(toolbar(chips.node, h('span', 'kit-spacer'), classSelect.node, picker.node, studentSlot));

  const list = pagedList({
    app,
    path: HOMEWORK,
    query: () => ({ state: filters.state || undefined, classGroupId: filters.classGroupId || undefined,
      studentId: filters.studentId || undefined }),
    limit: 50,
    columns: [
      { key: 'title', label: 'Ödev', width: 'minmax(0, 2.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.title),
          h('small', '', [row.subject?.name, topicsText(row.topics)].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.6fr)', cell: (row) => targetText(row.target, names) },
      { key: 'due', label: 'Son teslim', width: '150px', cell: (row) => dueCell(row.dueAt) },
      { key: 'ratio', label: 'Teslim', width: '190px', cell: (row) => ratioCell(row.stats) },
      { key: 'by', label: 'Veren', width: 'minmax(0, 1fr)',
        cell: (row) => stack(row.createdBy?.displayName, fmtDate(row.createdAt)) },
    ],
    onRow: (row) => openDetail(row.id),
    empty: { title: 'Bu süzgece uyan ödev yok', text: 'Durum süzgecini kaldırın ya da yeni ödev verin.' },
  });
  node.append(list.node);

  function setStudent(id, label, reload = true) {
    filters.studentId = id || '';
    filters.studentLabel = label || '';
    studentSlot.replaceChildren();
    if (filters.studentId) {
      studentSlot.append(removableChip(filters.studentLabel || 'Seçili öğrenci', () => setStudent('', ''),
        'Öğrenci süzgecini kaldır'));
    }
    if (reload) list.reload();
  }

  function openDetail(id) {
    homeworkDrawer(app, id, { names, onChange: (next, removedId) => {
      if (next) list.replace((row) => row.id === next.id, next);
      else if (removedId) list.remove((row) => row.id === removedId);
    } });
  }

  // Sınıf / grup adları önbellekten; liste paralel yüklenir, adlar gelince
  // yüklenen satırlar yeniden çizilir.
  function start() {
    list.reload();
    Promise.all([
      app.lookups.classGroups().catch(() => []),
      app.lookups.studentGroups().catch(() => []),
    ]).then(([classes, groups]) => {
      for (const item of classes) names.classes.set(item.id, item.name);
      for (const item of groups) names.groups.set(item.id, item.name);
      classSelect.options([{ value: '', label: 'Tüm sınıflar' },
        ...classes.map((item) => ({ value: item.id, label: item.name }))]);
      classSelect.set(filters.classGroupId);
      list.table.update({ rows: list.rows() });
    });
  }

  return {
    node,
    shown(payload) {
      let changed = false;
      if (payload?.studentId) {
        // Öğrenciden gelindi: bütün durumlar görünsün.
        filters.state = '';
        chips.set(null);
        setStudent(payload.studentId, payload.studentName || '', false);
        changed = true;
      }
      if (payload?.classGroupId) {
        filters.classGroupId = payload.classGroupId;
        classSelect.set(payload.classGroupId);
        changed = true;
      }
      if (!started) { started = true; start(); } else if (changed) list.reload();
      if (payload?.homeworkId) openDetail(payload.homeworkId);
    },
    destroy() { picker.destroy(); list.destroy(); },
  };
}

// ---------------------------------------------------------------- ayrıntı

function homeworkDrawer(app, id, { names, onChange } = {}) {
  const panel = openDrawer(app, { title: 'Ödev' });
  panel.node.classList.add('ak-hw-drawer');
  let homework = null;
  let submissions = [];
  let filter = null;
  let table = null;
  let chips = null;

  const view = loader(() => Promise.all([
    app.api.get(`/v1/admin/homework/${id}`),
    app.api.get(`/v1/admin/homework/${id}/submissions`),
  ]), ([item, subs]) => {
    homework = item;
    submissions = subs?.items || [];
    panel.setTitle(item.title || 'Ödev');
    return render();
  }, { rows: 6, columns: 5 });
  panel.body.append(view.node);
  view.reload();

  const counts = () => {
    const out = { done: 0, late: 0, overdue: 0, pending: 0 };
    for (const row of submissions) out[submissionState(row)] += 1;
    return out;
  };
  const missingRows = () => submissions.filter((row) => row.status !== 'completed');

  function render() {
    const tally = counts();
    const missing = tally.overdue + tally.pending;
    const submitted = tally.done + tally.late;
    const assigned = submissions.length || homework.stats?.assignedCount || 0;
    const nodes = [];

    const tools = h('div', 'ak-inline');
    tools.append(
      missing
        ? actionButton(app, 'admin_remindHomework', 'Teslim etmeyenlere hatırlat', { variant: 'primary',
          onClick: () => remind([]) })
        : blockedButton('Teslim etmeyenlere hatırlat', 'Teslim etmeyen öğrenci yok.', { variant: 'primary' }),
      actionButton(app, 'admin_updateHomework', 'Düzenle', { onClick: () => edit() }),
      actionButton(app, 'admin_deleteHomework', 'Sil', { variant: 'ghost', onClick: () => remove() }),
    );
    nodes.push(tools);
    if (isPast(homework.dueAt) && missing) {
      nodes.push(alertBox(`Son teslim geçti; ${num(missing)} öğrenci teslim etmedi.`, 'warn'));
    }
    nodes.push(kpiRow([
      { label: 'Atanan', value: num(assigned) },
      { label: 'Teslim eden', value: num(submitted), tone: submitted && submitted === assigned ? 'good' : '' },
      { label: 'Geç teslim', value: num(tally.late), tone: tally.late ? 'warn' : '' },
      { label: 'Teslim etmeyen', value: num(missing), tone: tally.overdue ? 'bad' : '',
        title: `${num(tally.overdue)} süresi geçmiş · ${num(tally.pending)} bekliyor` },
      { label: 'Teslim oranı', value: assigned ? percent((submitted / assigned) * 100, 0) : '—' },
    ]));

    const resourceSlot = h('div', 'ak-cell-stack');
    paintResource(resourceSlot);
    const description = h('div', 'ak-hw-text', homework.description || '—');
    nodes.push(card('', kv([
      ['Ders', homework.subject?.name || '—'],
      ['Konular', topicsText(homework.topics, 50) || '—'],
      ['Kaynak', resourceSlot],
      ['Hedef', targetText(homework.target, names, { full: true })],
      ['Son teslim', `${fmtDateTime(homework.dueAt)}${isPast(homework.dueAt) ? ' · süresi geçti' : ''}`],
      ['Veren', `${homework.createdBy?.displayName || '—'} · ${fmtDateTime(homework.createdAt)}`],
      ['Açıklama', description],
    ])));

    chips = chipRow([
      { key: 'submitted', label: 'Teslim eden', count: submitted },
      { key: 'late', label: 'Geç teslim', count: tally.late },
      { key: 'missing', label: 'Teslim etmeyen', count: missing },
    ], filter, (key) => { filter = key; paintTable(); });
    const remindSelected = actionButton(app, 'admin_remindHomework', 'Seçilenlere hatırlat', {
      onClick: () => {
        const chosen = new Set(table?.selection() || []);
        const ids = missingRows().map((row) => row.student.id).filter((studentId) => chosen.has(studentId));
        if (!chosen.size) { app.toast('Önce listeden öğrenci seçin.', 'warn'); return; }
        if (!ids.length) { app.toast('Seçilen öğrencilerin hepsi teslim etmiş.', 'warn'); return; }
        remind(ids);
      } });
    table = dataTable({
      columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.8fr)', cell: (row) => stack(row.student?.displayName,
          [row.student?.classGroup?.name, row.student?.externalRef].filter(Boolean).join(' · ')) },
        { key: 'status', label: 'Durum', cell: (row) => statusBadge(SUBMISSION, submissionState(row)) },
        { key: 'at', label: 'Teslim', width: '130px', cell: (row) => fmtDateTime(row.completion?.completedAt) },
        { key: 'score', label: 'D / Y / B', align: 'num', cell: (row) => (row.completion
          ? `${num(row.completion.correct)} / ${num(row.completion.wrong)} / ${num(row.completion.blank)}` : '—') },
        { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)', cell: (row) => (row.completion?.note
          ? clip(h('span'), row.completion.note, 60) : '—') },
        { key: 'photos', label: '', width: '120px', cell: (row) => {
          const images = row.completion?.images || [];
          if (!images.length) return '';
          return button(`Fotoğraf (${num(images.length)})`, { variant: 'ghost',
            onClick: () => showPhotos(row.student.id) });
        } },
      ],
      rows: [],
      selectable: true,
      rowKey: (row) => row.student?.id,
      empty: empty('Bu süzgece uyan öğrenci yok'),
    });
    paintTable();
    const box = h('div', 'ak-stack ak-card-pad');
    box.append(toolbar(chips.node, h('span', 'kit-spacer'), remindSelected), table.node);
    nodes.push(card('Teslim durumu', box, 'D / Y / B: doğru, yanlış, boş'));
    return nodes;
  }

  function paintTable() {
    if (!table) return;
    const rows = submissions.filter((row) => {
      const state = submissionState(row);
      if (filter === 'submitted') return state === 'done' || state === 'late';
      if (filter === 'late') return state === 'late';
      if (filter === 'missing') return state === 'overdue' || state === 'pending';
      return true;
    }).sort((a, b) => (SUBMISSION_ORDER[submissionState(a)] - SUBMISSION_ORDER[submissionState(b)])
      || String(a.student?.displayName || '').localeCompare(String(b.student?.displayName || ''), 'tr'));
    table.update({ rows });
  }

  async function paintResource(slot) {
    const pages = homework.pageFrom || homework.pageTo
      ? `s. ${homework.pageFrom ?? '…'}–${homework.pageTo ?? '…'}` : '';
    if (!homework.resourceId) {
      slot.append(h('span', '', pages || '—'));
      return;
    }
    slot.append(h('span', 'ak-muted', 'Yükleniyor…'));
    try {
      const resource = await app.api.getCached(`/v1/catalog/library/${homework.resourceId}`);
      const sectionTitles = new Map();
      for (const unit of resource.toc || []) {
        for (const item of unit.sections || []) sectionTitles.set(item.id, item.title);
      }
      const sections = (homework.sectionIds || []).map((sectionId) => sectionTitles.get(sectionId))
        .filter(Boolean);
      slot.replaceChildren(h('span', '', [resource.title, resource.publisher].filter(Boolean).join(' · ')));
      const detail = [sections.length ? sections.join(', ') : '', pages].filter(Boolean).join(' · ');
      if (detail) slot.append(h('small', '', detail));
    } catch (failure) {
      slot.replaceChildren(h('span', 'ak-muted', `Kaynak bilgisi alınamadı (${problem(failure)})`));
      if (pages) slot.append(h('small', '', pages));
    }
  }

  async function refreshSubmissions() {
    const data = await app.api.get(`/v1/admin/homework/${id}/submissions`);
    submissions = data?.items || [];
    paintTable();
  }

  async function showPhotos(studentId) {
    let row = submissions.find((item) => item.student?.id === studentId);
    const stale = (row?.completion?.images || []).some((image) => image.url && image.urlExpiresAt
      && new Date(image.urlExpiresAt).getTime() - Date.now() < 15_000);
    if (stale) {
      const ok = await runAction(app, async () => { await refreshSubmissions(); return true; });
      if (!ok) return;
      row = submissions.find((item) => item.student?.id === studentId);
    }
    if (row) photoViewer(app, row);
  }

  async function edit() {
    const studentNames = new Map(submissions.map((row) => [row.student?.id, row.student?.displayName]));
    await editHomework(app, homework, { studentNames, onSaved: (saved) => {
      onChange?.(saved);
      view.reload();
    } });
  }

  async function remove() {
    const yes = await confirmAction(app, { title: `“${homework.title}” silinsin mi?`,
      description: 'Ödev öğrencilerin ve velilerin listesinden kalkar. Geri alma yoktur.',
      confirmLabel: 'Ödevi sil', danger: true });
    if (!yes) return;
    const ok = await runAction(app, async () => { await app.api.del(`/v1/admin/homework/${id}`); return true; },
      { success: 'Ödev silindi.' });
    if (ok) {
      panel.close();
      onChange?.(null, id);
    }
  }

  async function remind(studentIds) {
    const count = studentIds.length || missingRows().length;
    const result = await formDialog(app, {
      title: studentIds.length ? `${num(count)} öğrenciye hatırlatma` : 'Teslim etmeyenlere hatırlat',
      description: `“${homework.title}” için teslim etmeyen ${num(count)} öğrenciye uygulama bildirimi `
        + 'gönderilir.',
      value: { includeGuardians: true },
      fields: [{ key: 'includeGuardians', label: 'Velilerine de gönder', type: 'checkbox' }],
      submitLabel: 'Hatırlat',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/homework/${id}/remind`, {
        studentIds: studentIds.length ? studentIds : undefined,
        includeGuardians: Boolean(draft.includeGuardians),
      }, { key }),
    });
    if (result) app.toast(`${num(result.notifiedCount ?? 0)} kişiye hatırlatma gönderildi.`, 'good');
  }

  return panel;
}

/** Teslim fotoğrafları: imzalı bağlantı köprüden indirilir, nesne URL'si kapanınca bırakılır. */
function photoViewer(app, row) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog ak-dialog wide');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title', `${row.student?.displayName || 'Öğrenci'} — teslim fotoğrafları`));
  const grid = h('div', 'ak-hw-photos');
  const urls = [];
  let closed = false;
  const close = () => {
    closed = true;
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    for (const url of urls) URL.revokeObjectURL(url);
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  for (const image of row.completion?.images || []) {
    const cell = h('figure', 'ak-hw-photo');
    grid.append(cell);
    if (image.hidden || !image.url) {
      cell.append(h('div', 'ak-muted', 'Gizlilik kuralı nedeniyle görüntülenemiyor.'));
      continue;
    }
    cell.append(skeletonRows(1, 1));
    app.api.fetchFile(image.url, `teslim-${image.id}`).then(({ blob }) => {
      const url = URL.createObjectURL(blob);
      if (closed) { URL.revokeObjectURL(url); return; }
      urls.push(url);
      const img = h('img');
      img.src = url;
      img.alt = 'Teslim fotoğrafı';
      cell.replaceChildren(img);
    }).catch((failure) => {
      cell.replaceChildren(h('div', 'ak-muted', `${problem(failure, 'Fotoğraf alınamadı.')} `
        + 'Bağlantının süresi dolmuş olabilir; pencereyi kapatıp yeniden açın.'));
    });
  }
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Kapat', { onClick: close }));
  box.append(grid, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  app.root.append(overlay);
}

// ------------------------------------------------------------------ form

/**
 * Ödev ver / düzenle. Hedef üç sekmeli (sınıf · grup · öğrenci; hepsi
 * birlikte gönderilir), konu seçimi dersin programından, kaynak hazır
 * kütüphaneden. Öğrenci adları liste yükünde olmadığı için düzenlemede
 * teslim tablosundaki adlar kullanılır (`studentNames`).
 */
async function editHomework(app, current, { studentNames = new Map(), onSaved } = {}) {
  const [classes, groups, subjects] = await Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
    app.lookups.subjects().catch(() => []),
  ]);
  const disposers = [];
  const due = current ? istanbulParts(current.dueAt) : { day: addDays(todayIst(), 7), clock: '23:59' };
  let clockControl = null;
  const target = {
    classGroupIds: [...(current?.target?.classGroupIds || [])],
    studentGroupIds: [...(current?.target?.studentGroupIds || [])],
    students: new Map((current?.target?.studentIds || []).map((studentId) => [studentId,
      studentNames.get(studentId) || 'Öğrenci'])),
  };
  const topics = new Map((current?.topics || []).map((topic) => [topic.id, topic.name]));
  const resource = {
    id: current?.resourceId || null,
    sectionIds: new Set(current?.sectionIds || []),
    pageFrom: current?.pageFrom ?? null,
    pageTo: current?.pageTo ?? null,
  };
  const subjectOptions = [{ value: '', label: 'Ders seçilmedi' },
    ...subjects.map((item) => ({ value: item.id, label: item.name }))];
  if (current?.subject && !subjects.some((item) => item.id === current.subject.id)) {
    subjectOptions.push({ value: current.subject.id, label: current.subject.name });
  }

  // ------------------------------------------------------- son teslim
  const dueField = (draft, set) => {
    const wrap = h('div', 'ak-inline ak-hw-due');
    const day = dateInput(due.day, (iso) => { due.day = iso || ''; set(due.day); }, 'Son teslim günü');
    disposers.push(() => day.destroy());
    clockControl = clockInput(due.clock, (value) => { due.clock = value; }, 'Son teslim saati');
    wrap.append(day.node, clockControl.node, h('span', 'ak-muted', 'İstanbul saati'));
    set(due.day);
    return wrap;
  };
  const dueValidate = () => {
    if (!due.day) return 'Son teslim günü zorunlu.';
    const clock = clockControl?.value || due.clock;
    if (!CLOCK.test(clock)) return 'Saati 18:30 gibi yazın.';
    if (!current && new Date(istanbulStamp(due.day, clock)).getTime() < Date.now()) {
      return 'Son teslim geçmiş bir zaman olamaz.';
    }
    return null;
  };

  // ------------------------------------------------------------- hedef
  const targetField = (draft, set) => {
    const wrap = h('div', 'ak-stack ak-hw-target');
    const summary = h('div', 'ak-muted');
    const panes = { classes: h('div', 'ak-hw-pane'), groups: h('div', 'ak-hw-pane'),
      students: h('div', 'ak-stack ak-hw-pane') };
    const initial = target.students.size && !target.classGroupIds.length && !target.studentGroupIds.length
      ? 'students' : (target.studentGroupIds.length && !target.classGroupIds.length ? 'groups' : 'classes');
    const tabs = tabBar([
      { key: 'classes', label: 'Sınıflar' },
      { key: 'groups', label: 'Çalışma grupları' },
      { key: 'students', label: 'Öğrenciler' },
    ], initial, (key) => show(key));
    const show = (key) => { for (const [name, pane] of Object.entries(panes)) pane.hidden = name !== key; };
    const sync = () => {
      const parts = [];
      if (target.classGroupIds.length) parts.push(`${num(target.classGroupIds.length)} sınıf`);
      if (target.studentGroupIds.length) parts.push(`${num(target.studentGroupIds.length)} grup`);
      if (target.students.size) parts.push(`${num(target.students.size)} öğrenci`);
      summary.textContent = parts.length ? `Hedef: ${parts.join(', ')}. Akademi bunları birleştirir; aynı `
        + 'öğrenciye ödev bir kez düşer.' : 'Henüz hedef seçilmedi.';
      tabs.badge('classes', target.classGroupIds.length);
      tabs.badge('groups', target.studentGroupIds.length);
      tabs.badge('students', target.students.size);
      set(parts.length ? parts.join(',') : '');
    };

    panes.classes.append(classes.length
      ? multiChips(classes.map((item) => ({ value: item.id, label: item.name })), target.classGroupIds, (ids) => {
        target.classGroupIds = ids;
        sync();
      }).node
      : h('span', 'ak-muted', 'Sınıf bulunamadı.'));
    panes.groups.append(groups.length
      ? multiChips(groups.map((item) => ({ value: item.id, label: item.name })), target.studentGroupIds, (ids) => {
        target.studentGroupIds = ids;
        sync();
      }).node
      : h('span', 'ak-muted', 'Çalışma grubu yok.'));

    const picked = h('div', 'ak-chips');
    const paintPicked = () => {
      picked.replaceChildren();
      for (const [studentId, name] of target.students) {
        picked.append(removableChip(name, () => {
          target.students.delete(studentId);
          paintPicked();
          sync();
        }));
      }
      if (!target.students.size) picked.append(h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
    };
    const picker = studentPicker(app, { placeholder: 'Öğrenci ekle (ad, okul no)', onPick: (student) => {
      target.students.set(student.id, student.displayName || 'Öğrenci');
      paintPicked();
      sync();
    } });
    disposers.push(() => picker.destroy());
    panes.students.append(picker.node, picked);
    paintPicked();

    wrap.append(tabs.node, panes.classes, panes.groups, panes.students, summary);
    show(initial);
    sync();
    return wrap;
  };

  // ------------------------------------------------------------- konular
  const topicField = (draft) => {
    const wrap = h('div', 'ak-stack');
    const picked = h('div', 'ak-chips');
    const programSelect = selectBox([{ value: '', label: 'Program seçin' }], '', (programId) => loadProgram(programId),
      { label: 'Program' });
    const topicSlot = h('div', 'ak-stack ak-hw-topics');
    let subjectShown;
    let unitChips = [];

    const refreshChips = () => {
      for (const { chips, live } of unitChips) chips.set(live.filter((topic) => topics.has(topic.id)).map((topic) => topic.id));
    };
    const paintPicked = () => {
      picked.replaceChildren();
      for (const [topicId, name] of topics) {
        picked.append(removableChip(name, () => {
          topics.delete(topicId);
          paintPicked();
          refreshChips();
        }));
      }
      if (!topics.size) picked.append(h('span', 'ak-muted', 'Konu seçilmedi.'));
    };

    async function loadSubject(subjectId) {
      if (subjectId === subjectShown) return;
      subjectShown = subjectId;
      unitChips = [];
      topicSlot.replaceChildren();
      programSelect.node.hidden = !subjectId;
      if (!subjectId) {
        topicSlot.append(h('span', 'ak-muted', 'Konu seçmek için önce ders seçin.'));
        return;
      }
      programSelect.options([{ value: '', label: 'Programlar yükleniyor…' }]);
      try {
        const data = await app.api.getCached(PROGRAMS, { subject: subjectId });
        if (subjectShown !== subjectId) return;
        const programs = data?.items || [];
        programSelect.options([{ value: '', label: programs.length ? 'Program seçin (seviye / sınav)'
          : 'Bu derste program yok' },
        ...programs.map((item) => ({ value: item.id, label: `${programLevel(item)} — ${item.title}` }))]);
        programSelect.set(programs.length === 1 ? programs[0].id : '');
        if (programs.length === 1) loadProgram(programs[0].id);
      } catch (failure) {
        topicSlot.replaceChildren(errorBox(failure));
      }
    }

    async function loadProgram(programId) {
      unitChips = [];
      topicSlot.replaceChildren();
      if (!programId) return;
      topicSlot.append(skeletonRows(3, 3));
      try {
        const program = await app.api.getCached(`/v1/admin/curricula/programs/${programId}`);
        if (programSelect.value !== programId) return;
        topicSlot.replaceChildren();
        const units = [...(program.units || [])].filter((unit) => !unit.retired)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        for (const unit of units) {
          const live = (unit.topics || []).filter((topic) => !topic.retired)
            .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
          if (!live.length) continue;
          const block = h('div', 'ak-hw-unit');
          const chips = multiChips(live.map((topic) => ({ value: topic.id, label: topic.title })),
            live.filter((topic) => topics.has(topic.id)).map((topic) => topic.id), (ids) => {
              const chosen = new Set(ids);
              for (const topic of live) {
                if (chosen.has(topic.id)) topics.set(topic.id, topic.title); else topics.delete(topic.id);
              }
              paintPicked();
            });
          unitChips.push({ chips, live });
          block.append(h('div', 'ak-hw-unit-title', unit.title), chips.node);
          topicSlot.append(block);
        }
        if (!unitChips.length) topicSlot.append(h('span', 'ak-muted', 'Bu programda konu yok.'));
      } catch (failure) {
        topicSlot.replaceChildren(errorBox(failure));
      }
    }

    paintPicked();
    wrap.append(picked, programSelect.node, topicSlot);
    loadSubject(draft.subjectId || '');
    return { node: wrap, update: (next) => loadSubject(next.subjectId || '') };
  };

  // ------------------------------------------------------------- kaynak
  const resourceField = (draft) => {
    const wrap = h('div', 'ak-stack');
    const chosen = h('div', 'ak-stack');
    const finder = h('div', 'ak-picker');
    const results = h('div', 'ak-picker-list');
    results.hidden = true;
    let token = 0;
    const box = searchBox({ placeholder: 'Kaynak ara (kitap adı, yayınevi ya da ISBN)', onSearch: async (text) => {
      const mine = ++token;
      if (text.length < 2) { results.hidden = true; return; }
      const digits = text.replace(/[\s-]/g, '');
      const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
      try {
        const data = await app.api.get('/v1/catalog/library', { ...query,
          subject: draft.subjectId || undefined, limit: 12 });
        if (mine !== token) return;
        results.replaceChildren();
        const items = data?.items || [];
        if (!items.length) {
          results.append(h('div', 'ak-picker-empty', draft.subjectId
            ? 'Bu derste eşleşen kaynak yok. Kaynaklar ekranından eklenebilir.' : 'Eşleşen kaynak yok.'));
        }
        for (const item of items) {
          const row = h('button', 'ak-picker-row');
          row.type = 'button';
          row.append(h('b', '', item.title), h('span', '', [item.publisher, RESOURCE_KINDS[item.kind],
            `${num(item.sectionCount)} bölüm`, item.scope === 'tenant' ? 'kuruma özel' : ''].filter(Boolean).join(' · ')));
          row.addEventListener('click', () => {
            results.hidden = true;
            if (resource.id !== item.id) {
              resource.id = item.id;
              resource.sectionIds.clear();
              resource.pageFrom = null;
              resource.pageTo = null;
            }
            paintChosen();
          });
          results.append(row);
        }
        results.hidden = false;
      } catch (failure) {
        if (mine !== token) return;
        results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
        results.hidden = false;
      }
    } });
    disposers.push(() => box.destroy());
    finder.append(box.node, results);

    async function paintChosen() {
      chosen.replaceChildren();
      finder.hidden = Boolean(resource.id);
      if (!resource.id) {
        chosen.append(h('span', 'ak-muted', 'Kaynak seçilmedi. Hazır kütüphaneden ya da kurumun kaynaklarından '
          + 'arayın.'));
        return;
      }
      const forId = resource.id;
      chosen.append(skeletonRows(2, 3));
      let detail;
      try {
        detail = await app.api.getCached(`/v1/catalog/library/${forId}`);
      } catch (failure) {
        if (resource.id !== forId) return;
        chosen.replaceChildren(errorBox(failure, () => paintChosen()), clearButton());
        return;
      }
      if (resource.id !== forId) return;
      const head = h('div', 'ak-inline');
      head.append(stack(detail.title, [detail.publisher, RESOURCE_KINDS[detail.kind], detail.isbn]
        .filter(Boolean).join(' · ')), h('span', 'kit-spacer'), clearButton());

      const toc = h('div', 'ak-hw-toc');
      for (const unit of [...(detail.toc || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
        const sections = [...(unit.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        if (!sections.length) continue;
        toc.append(h('div', 'ak-hw-unit-title', unit.title));
        for (const item of sections) {
          const row = h('label', 'ak-hw-section');
          const check = h('input', 'kit-check');
          check.type = 'checkbox';
          check.checked = resource.sectionIds.has(item.id);
          check.addEventListener('change', () => {
            if (check.checked) resource.sectionIds.add(item.id); else resource.sectionIds.delete(item.id);
          });
          const pages = item.pageFrom || item.pageTo ? `s. ${item.pageFrom ?? '…'}–${item.pageTo ?? '…'}` : '';
          row.append(check, h('span', 'ak-hw-section-title', item.title),
            h('small', 'ak-muted', [SECTION_KINDS[item.kind], pages,
              item.questionCount ? `${num(item.questionCount)} soru` : ''].filter(Boolean).join(' · ')));
          toc.append(row);
        }
      }
      if (!toc.childElementCount) toc.append(h('span', 'ak-muted', 'Bu kaynağın içindekiler bilgisi yok.'));

      const pageRow = h('div', 'ak-inline ak-hw-pages');
      const pageInput = (value, onValue, label) => {
        const input = h('input', 'kit-input ak-hw-page');
        input.inputMode = 'numeric';
        input.placeholder = label;
        input.setAttribute('aria-label', label);
        input.value = value ?? '';
        input.addEventListener('input', () => {
          const parsed = Number.parseInt(input.value, 10);
          onValue(Number.isFinite(parsed) && parsed > 0 ? parsed : null);
        });
        return input;
      };
      pageRow.append(h('span', 'ak-muted', 'Sayfa aralığı (isteğe bağlı):'),
        pageInput(resource.pageFrom, (value) => { resource.pageFrom = value; }, 'ilk sayfa'),
        h('span', '', '–'),
        pageInput(resource.pageTo, (value) => { resource.pageTo = value; }, 'son sayfa'));
      chosen.replaceChildren(head, toc, pageRow);
    }

    function clearButton() {
      return button('Kaynağı kaldır', { variant: 'ghost', onClick: () => {
        resource.id = null;
        resource.sectionIds.clear();
        resource.pageFrom = null;
        resource.pageTo = null;
        paintChosen();
      } });
    }

    paintChosen();
    wrap.append(chosen, finder);
    return wrap;
  };

  // ----------------------------------------------------- açıklama / bildirim
  const descriptionField = (draft, set) => {
    const area = h('textarea', 'kit-textarea');
    area.maxLength = 1000;
    area.placeholder = 'Ör. “Testleri çözüp yanlışlarınızın fotoğrafını yükleyin.”';
    area.value = draft.description || '';
    area.addEventListener('input', () => set(area.value));
    return area;
  };
  const notifyField = (draft, set) => {
    const label = h('label', 'ak-inline');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = Boolean(draft.notify);
    check.addEventListener('change', () => set(check.checked));
    label.append(check, h('span', '', current ? 'Değişikliği öğrencilere bildir'
      : 'Öğrencilere uygulama bildirimi gönder'));
    return label;
  };

  const saved = await formDialog(app, {
    title: current ? 'Ödevi düzenle' : 'Yeni ödev',
    wide: true,
    value: current
      ? { title: current.title, subjectId: current.subject?.id || '', description: current.description || '',
        notify: false }
      : { subjectId: '', notify: true },
    fields: [
      { key: 'title', label: 'Başlık', required: true, maxLength: 120, wide: true,
        placeholder: 'Ör. Paragraf — Test 4-6' },
      { key: 'subjectId', label: 'Ders', type: 'select', options: subjectOptions },
      { key: 'due', label: 'Son teslim', type: 'custom', required: true, render: dueField,
        validate: dueValidate },
      { key: 'target', label: 'Hedef', type: 'custom', render: targetField,
        validate: () => (target.classGroupIds.length || target.studentGroupIds.length || target.students.size
          ? null : 'En az bir sınıf, grup ya da öğrenci seçin.') },
      { key: 'topicIds', label: 'Müfredat konuları (isteğe bağlı)', type: 'custom', render: topicField },
      { key: 'resource', label: 'Kaynak (isteğe bağlı)', type: 'custom', render: resourceField,
        validate: () => (resource.id && resource.pageFrom && resource.pageTo && resource.pageFrom > resource.pageTo
          ? 'İlk sayfa son sayfadan büyük olamaz.' : null) },
      { key: 'description', label: 'Açıklama', type: 'custom', render: descriptionField },
      { key: 'notify', label: 'Bildirim', type: 'custom', render: notifyField },
    ],
    submitLabel: current ? 'Kaydet' : 'Ödevi ver',
    onSubmit: (draft, { key }) => {
      const clock = clockControl?.value && CLOCK.test(clockControl.value) ? clockControl.value : due.clock;
      const body = {
        title: String(draft.title || '').trim(),
        description: String(draft.description || '').trim() || null,
        target: {
          classGroupIds: target.classGroupIds,
          studentGroupIds: target.studentGroupIds,
          studentIds: [...target.students.keys()],
        },
        subjectId: draft.subjectId || null,
        resourceId: resource.id || null,
        sectionIds: resource.id ? [...resource.sectionIds] : [],
        pageFrom: resource.id ? resource.pageFrom : null,
        pageTo: resource.id ? resource.pageTo : null,
        topicIds: [...topics.keys()],
        dueAt: istanbulStamp(due.day, clock),
        notify: Boolean(draft.notify),
      };
      return current ? app.api.put(`/v1/admin/homework/${current.id}`, body)
        : app.api.post(HOMEWORK, body, { key });
    },
  });
  for (const fn of disposers) fn();
  if (!saved) return;
  app.toast(current ? 'Ödev güncellendi.' : 'Ödev verildi.', 'good');
  onSaved?.(saved);
}
