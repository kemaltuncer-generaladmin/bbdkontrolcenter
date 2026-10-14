// BBD Akademi — Ödevler (F-A9).
//
// Ödev = hedef (sınıf · çalışma grubu · tek tek öğrenci; üçü birlikte de
// olabilir, Akademi birleştirir) + ders + isteğe bağlı müfredat konuları +
// isteğe bağlı kaynak (hazır kütüphane ya da kuruma özel kaynak; bölümler ve
// sayfa aralığı) + son teslim (Europe/Istanbul) + açıklama. Teslim tablosu ve
// "Teslim etmeyenlere hatırlat" ayrıntı çekmecesindedir.
//
// ÖĞRETMEN KONTROLÜ (U20, Akademi README §14 madde 77): teslim satırında
// "Kontrol edildi / Eksik / Yapmadı" + kısa not (öğrenci ve veli görür);
// öğrencinin beyanından ayrıdır, teslim etmemiş öğrenci de işaretlenir.
// Satırdan tek tek (`admin_reviewHomeworkSubmission`, geri al
// `admin_clearHomeworkSubmissionReview`), seçilenlere ya da "Teslim edenler
// kontrol edildi" ile toplu (`admin_reviewHomeworkSubmissions`). Metinler
// uygulama (Flutter + React) ile aynı.
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
  actionButton, addDays, clockInput, confirmAction, dateInput, empty, fmtDate,
  fmtDateTime, formDialog, istanbulStamp, kv, loader, multiChips, num, openDrawer,
  pagedList, percent, problem, runAction, sectionHead, selectBox, statusBadge,
  studentPicker, todayIst, toolbar, TZ,
} from './common.js';
import { homeworkContent, removableChip, stack, topicsText } from './homework-content.js';
import { bulkHomeworkWizard } from './homework-bulk.js';
import { documentButtons } from './documents.js';
import { homeworkListDoc, studentHomeworkDoc } from './documents-logic.js';

const HOMEWORK = '/v1/admin/homework';

const SUBMISSION = { done: ['Teslim etti', 'good'], late: ['Geç teslim', 'warn'],
  overdue: ['Teslim etmedi', 'bad'], pending: ['Bekliyor', 'info'] };
const SUBMISSION_ORDER = { overdue: 0, pending: 1, late: 2, done: 3 };
const REVIEW = { checked: ['Kontrol edildi', 'good'], incomplete: ['Eksik', 'warn'],
  not_done: ['Yapmadı', 'bad'], none: ['Kontrol bekliyor', 'info'] };
const REVIEW_KEYS = ['checked', 'incomplete', 'not_done'];
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

function isPast(stamp) {
  const time = new Date(String(stamp || '')).getTime();
  return !Number.isNaN(time) && time < Date.now();
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

function ratioCell(stats, dueAt) {
  const assigned = stats?.assignedCount || 0;
  const done = stats?.completedCount || 0;
  const share = assigned ? Math.min(1, done / assigned) : 0;
  const cell = h('div', 'ak-hw-ratio');
  const line = h('div', 'ak-hw-ratio-line');
  line.append(h('span', 'ak-num', assigned ? `${num(done)}/${num(assigned)}` : '—'));
  if (assigned) line.append(h('span', 'ak-muted', percent(share * 100, 0)));
  if (stats?.overdueCount) line.append(badge(`${num(stats.overdueCount)} gecikmiş`, 'bad'));
  // Öğretmen kontrolü: süresi geçmiş ödevde kontrol bekleyen satır (eski Akademi alanı göndermezse yok).
  const toReview = assigned - (stats?.reviewedCount ?? assigned);
  if (isPast(dueAt) && toReview > 0) line.append(badge(`${num(toReview)} kontrol bekliyor`, 'info'));
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
  const filters = { state: 'active', classGroupId: '', studentId: '', studentLabel: '', seriesId: '' };
  let started = false;
  let selected = [];

  // Tek ödev de, onlarca sınıfa haftalık seri de AYNI üç adımdan verilir (ne → kime → ne zaman).
  const compose = (initial = {}) => bulkHomeworkWizard(app, initial).then((job) => {
    if (job?.homework?.seriesId) setSeries(job.homework.seriesId);
    else if (job) list.reload();
  });
  const add = actionButton(app, 'admin_createHomeworkBulk', 'Ödev ver', { variant: 'primary',
    onClick: () => compose() });
  node.append(sectionHead('Ödevler', {
    subtitle: 'Sınıfa, çalışma grubuna ya da öğrenciye — tek ödev ya da toplu, şablondan ya da yeni; '
      + 'teslim durumu ve hatırlatma.',
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
  const seriesSlot = h('span', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz',
    onPick: (student) => setStudent(student.id, student.displayName || '') });
  // Belge: öğrenci süzülmüşse o öğrencinin ödevleri (durum + sonuç), yoksa süzgeçteki
  // ödev listesi (sınıf / süre). Liste sayfalı okunur (A14).
  const homeworkDoc = documentButtons(app, { title: 'Ödev listesi', label: 'Ödev listesi',
    build: async (progress) => {
      progress(0, 0, 'Ödevler okunuyor…');
      if (filters.studentId) {
        const [detail, items] = await Promise.all([
          app.api.get(`/v1/admin/students/${filters.studentId}`),
          app.api.all(`/v1/students/${filters.studentId}/homework`, {}, { limit: 100, max: 1000 })]);
        return studentHomeworkDoc(detail?.student || { displayName: filters.studentLabel }, items);
      }
      const rows = await app.api.all(HOMEWORK, { state: filters.state || undefined,
        classGroupId: filters.classGroupId || undefined, seriesId: filters.seriesId || undefined },
      { limit: 100, max: 2000 });
      return homeworkListDoc(rows, { scope: filters.classGroupId ? names.classes.get(filters.classGroupId)
        || classSelect.node.selectedOptions?.[0]?.textContent || '' : '',
      state: filters.state === 'active' ? 'Süresi dolmamış' : filters.state === 'past' ? 'Süresi geçmiş' : '' });
    } });
  node.append(toolbar(chips.node, h('span', 'kit-spacer'), classSelect.node, picker.node, studentSlot,
    seriesSlot, homeworkDoc));

  // Seçilenlere toplu işlem (en çok 200 ödev; `admin_bulkUpdateHomework`).
  const bulkBar = h('div', 'ak-hw-bulk');
  bulkBar.setAttribute('role', 'region');
  bulkBar.setAttribute('aria-label', 'Seçilen ödevler');
  const bulkCount = h('span', 'ak-hw-bulk-count');
  bulkCount.setAttribute('aria-live', 'polite');
  const bulkButtons = [
    actionButton(app, 'admin_bulkUpdateHomework', 'Ertele', { onClick: () => bulkAction('postpone') }),
    actionButton(app, 'admin_bulkUpdateHomework', 'Teslim tarihini değiştir',
      { onClick: () => bulkAction('set_due') }),
    actionButton(app, 'admin_bulkUpdateHomework', 'İptal et', { onClick: () => bulkAction('cancel') }),
    actionButton(app, 'admin_bulkUpdateHomework', 'Sil', { variant: 'ghost', onClick: () => bulkAction('delete') }),
  ];
  bulkBar.append(bulkCount, ...bulkButtons, button('Seçimi temizle', { variant: 'ghost', onClick: () => {
    list.table.clearSelection();
    setSelected([]);
  } }));
  bulkBar.hidden = true;
  node.append(bulkBar);

  const list = pagedList({
    app,
    path: HOMEWORK,
    query: () => ({ state: filters.state || undefined, classGroupId: filters.classGroupId || undefined,
      studentId: filters.studentId || undefined, seriesId: filters.seriesId || undefined }),
    limit: 50,
    selectable: true,
    onSelect: (keys) => setSelected(keys),
    onLoaded: () => setSelected(list.table.selection()),
    columns: [
      { key: 'title', label: 'Ödev', width: 'minmax(0, 2.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const title = h('b', '', row.title);
        cell.append(title,
          h('small', '', [row.subject?.name, topicsText(row.topics)].filter(Boolean).join(' · ')));
        if (row.status === 'cancelled') cell.append(badge('İptal edildi', 'dim'));
        return cell;
      } },
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.6fr)', cell: (row) => targetText(row.target, names) },
      { key: 'due', label: 'Son teslim', width: '150px', cell: (row) => dueCell(row.dueAt) },
      { key: 'ratio', label: 'Teslim', width: '190px', cell: (row) => ratioCell(row.stats, row.dueAt) },
      { key: 'by', label: 'Veren', width: 'minmax(0, 1fr)',
        cell: (row) => stack(row.createdBy?.displayName, fmtDate(row.createdAt)) },
    ],
    onRow: (row) => openDetail(row.id),
    // Boş durum yol gösterir: aynı "Yeni ödev" akışı (sınıfa, gruba ya da öğrenciye toplu).
    empty: { title: 'Henüz ödev yok ya da süzgece uyan yok',
      text: 'Sınıfa, gruba ya da seçtiğiniz öğrencilere tek seferde ödev verebilirsiniz.',
      actions: [actionButton(app, 'admin_createHomeworkBulk', 'Ödev ver', { variant: 'primary',
        onClick: () => compose() })] },
  });
  node.append(list.node);

  function setSelected(keys) {
    selected = [...keys];
    bulkBar.hidden = !selected.length;
    bulkCount.textContent = `${num(selected.length)} ödev seçili`;
  }

  /** Toplu ödev serisi süzgeci (sihirbazdan sonra ya da çekmecedeki "serinin hepsi"). */
  function setSeries(seriesId, reload = true) {
    filters.seriesId = seriesId || '';
    seriesSlot.replaceChildren();
    if (filters.seriesId) {
      // Yeni seri: süresi geçmiş / dolmamış ayrımı olmadan hepsi görünsün.
      filters.state = '';
      chips.set(null);
      seriesSlot.append(removableChip('Bu toplu ödev', () => setSeries(''), 'Seri süzgecini kaldır'));
    }
    list.table.clearSelection();
    setSelected([]);
    if (reload) list.reload();
  }

  async function bulkAction(action) {
    const ids = list.table.selection().slice(0, 200);
    if (!ids.length) { setSelected([]); return; }
    const result = await bulkUpdate(app, { homeworkIds: ids }, action, `${num(ids.length)} ödev`);
    if (!result) return;
    list.table.clearSelection();
    setSelected([]);
    list.reload();
  }

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
    homeworkDrawer(app, id, { names, onSeries: (seriesId) => setSeries(seriesId), onChange: (next, removedId) => {
      if (next) list.replace((row) => row.id === next.id, next);
      else if (removedId) list.remove((row) => row.id === removedId);
      else list.reload();
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
      // Öğrenci kartındaki "Ödev ver": sihirbaz o öğrenci seçili açılır.
      if (payload?.compose && app.can('admin_createHomeworkBulk')) {
        compose(payload.studentId ? { students: [{ id: payload.studentId,
          displayName: payload.studentName || 'Öğrenci' }] } : {});
      }
    },
    destroy() { picker.destroy(); list.destroy(); },
  };
}

// ---------------------------------------------------------------- ayrıntı

function homeworkDrawer(app, id, { names, onChange, onSeries } = {}) {
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
  const uncheckedDone = () => submissions.filter((row) => row.status === 'completed' && !row.review);

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
    if (uncheckedDone().length) {
      // Öğretmen kontrolü: teslim edip henüz işaretlenmemişlerin hepsi "Kontrol edildi" (tek istek).
      tools.prepend(actionButton(app, 'admin_reviewHomeworkSubmissions',
        `Teslim edenler kontrol edildi (${num(uncheckedDone().length)})`, {
          onClick: () => reviewMany(uncheckedDone().map((row) => row.student.id), 'checked') }));
    }
    if (homework.seriesId) {
      // Toplu ödevle (aynı seri) verilmiş: hepsini birlikte ertele / iptal et ya da listede gör.
      tools.append(h('span', 'kit-spacer'),
        button('Serinin hepsini göster', { variant: 'ghost', onClick: () => {
          panel.close();
          onSeries?.(homework.seriesId);
        } }),
        actionButton(app, 'admin_bulkUpdateHomework', 'Seriyi ertele', { variant: 'ghost',
          onClick: () => seriesAction('postpone') }),
        actionButton(app, 'admin_bulkUpdateHomework', 'Seriyi iptal et', { variant: 'ghost',
          onClick: () => seriesAction('cancel') }));
    }
    nodes.push(tools);
    if (homework.status === 'cancelled') {
      nodes.push(alertBox('Bu ödev toplu iptal edildi: öğrenci listesinde görünmez, teslimler saklanır.', 'info'));
    }
    if (isPast(homework.dueAt) && missing) {
      nodes.push(alertBox(`Son teslim geçti; ${num(missing)} öğrenci teslim etmedi.`, 'warn'));
    }
    const reviewed = submissions.filter((row) => row.review).length;
    nodes.push(kpiRow([
      { label: 'Atanan', value: num(assigned) },
      { label: 'Teslim eden', value: num(submitted), tone: submitted && submitted === assigned ? 'good' : '' },
      { label: 'Geç teslim', value: num(tally.late), tone: tally.late ? 'warn' : '' },
      { label: 'Teslim etmeyen', value: num(missing), tone: tally.overdue ? 'bad' : '',
        title: `${num(tally.overdue)} süresi geçmiş · ${num(tally.pending)} bekliyor` },
      { label: 'Teslim oranı', value: assigned ? percent((submitted / assigned) * 100, 0) : '—' },
      { label: 'Kontrol edilen', value: assigned ? `${num(reviewed)}/${num(assigned)}` : '—',
        tone: assigned && reviewed === assigned ? 'good' : '' },
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
      { key: 'unreviewed', label: 'Kontrol bekleyen', count: submissions.filter((row) => !row.review).length },
    ], filter, (key) => { filter = key; paintTable(); });
    const reviewSelected = REVIEW_KEYS.map((status) => actionButton(app, 'admin_reviewHomeworkSubmissions',
      `Seçilenler: ${REVIEW[status][0]}`, { variant: 'ghost', onClick: () => {
        const ids = table?.selection() || [];
        if (!ids.length) { app.toast('Önce listeden öğrenci seçin.', 'warn'); return; }
        reviewMany(ids, status);
      } }));
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
        { key: 'review', label: 'Kontrol', width: 'minmax(0, 1.4fr)', cell: (row) => reviewCell(row) },
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
    box.append(toolbar(chips.node, h('span', 'kit-spacer'), remindSelected),
      toolbar(h('span', 'ak-muted', 'Teslimi derste kontrol et: kontrol edildi, eksik ya da yapmadı. '
        + 'Öğrenci ve veli görür.'), h('span', 'kit-spacer'), ...reviewSelected), table.node);
    nodes.push(card('Teslim durumu', box, 'D / Y / B: doğru, yanlış, boş · Kontrol: öğretmenin işareti'));
    return nodes;
  }

  function paintTable() {
    if (!table) return;
    const rows = submissions.filter((row) => {
      const state = submissionState(row);
      if (filter === 'submitted') return state === 'done' || state === 'late';
      if (filter === 'late') return state === 'late';
      if (filter === 'missing') return state === 'overdue' || state === 'pending';
      if (filter === 'unreviewed') return !row.review;
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

  /** Kontrol hücresi: rozet + not; tıklayınca işaret penceresi (yetki yoksa yalnız rozet). */
  function reviewCell(row) {
    const review = row.review;
    const cell = h('div', 'ak-cell-stack');
    cell.append(statusBadge(REVIEW, review?.status || 'none'));
    if (review?.note) cell.append(clip(h('small'), review.note, 60));
    if (review) {
      cell.append(h('small', 'ak-muted',
        `${review.reviewedBy?.displayName || '—'} · ${fmtDateTime(review.reviewedAt)}`));
    }
    cell.append(actionButton(app, 'admin_reviewHomeworkSubmission', review ? 'Değiştir' : 'Kontrol et', {
      variant: 'ghost', onClick: () => reviewOne(row) }));
    return cell;
  }

  async function reviewOne(row) {
    const current = row.review;
    const result = await formDialog(app, {
      title: `Kontrol · ${row.student?.displayName || 'Öğrenci'}`,
      description: 'Öğrencinin beyanından ayrıdır; öğrenci her işarette, velisi eksik / yapmadı '
        + 'işaretinde bildirim alır.',
      value: { status: current?.status || 'checked', note: current?.note || '', clear: false },
      fields: [
        { key: 'status', label: 'Kontrol', type: 'select', required: true,
          options: REVIEW_KEYS.map((key) => ({ value: key, label: REVIEW[key][0] })) },
        { key: 'note', label: 'Öğretmen notu (isteğe bağlı)', type: 'textarea', maxLength: 300, wide: true,
          hint: 'Öğrenci ve veli görür.' },
        current && app.api.can('admin_clearHomeworkSubmissionReview')
          ? { key: 'clear', label: 'İşareti kaldır', type: 'checkbox' } : null,
      ],
      submitLabel: 'Kaydet',
      onSubmit: (draft) => {
        const path = `/v1/admin/homework/${id}/submissions/${row.student.id}/review`;
        if (draft.clear) return app.api.del(path);
        return app.api.put(path, { status: draft.status, note: String(draft.note || '').trim() || null });
      },
    });
    if (!result) return;
    app.toast(result.review ? `${REVIEW[result.review.status][0]} olarak işaretlendi.` : 'İşaret kaldırıldı.',
      'good');
    await afterReview();
  }

  async function reviewMany(studentIds, status) {
    const result = await runAction(app, () => app.api.post(`/v1/admin/homework/${id}/submissions/review`,
      { studentIds, status }, { key: `km-${globalThis.crypto?.randomUUID?.() || Date.now()}` }));
    if (!result) return;
    app.toast(`${num(result.items?.length ?? studentIds.length)} öğrenci işaretlendi.`, 'good');
    await afterReview();
  }

  async function afterReview() {
    view.reload();
    onChange?.(null, null);
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

  async function seriesAction(action) {
    const result = await bulkUpdate(app, { seriesId: homework.seriesId }, action, 'serideki bütün ödevler');
    if (!result) return;
    view.reload();
    onChange?.(null, null);
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

// ----------------------------------------------------------- toplu düzenle

const BULK_ACTIONS = {
  postpone: { title: 'Ertele', submit: 'Ertele', done: 'ertelendi' },
  set_due: { title: 'Teslim tarihini değiştir', submit: 'Tarihi kaydet', done: 'yeni tarihe alındı' },
  cancel: { title: 'İptal et', submit: 'İptal et', done: 'iptal edildi' },
  delete: { title: 'Sil', submit: 'Kalıcı olarak sil', done: 'silindi' },
};

/**
 * Toplu ertele / tarih / iptal / sil (`admin_bulkUpdateHomework`, tek işlem,
 * en çok 200 ödev). `scope`: {homeworkIds} ya da {seriesId}.
 * @returns {Promise<object|null>} sonuç ya da vazgeçildiyse null
 */
async function bulkUpdate(app, scope, action, what) {
  const spec = BULK_ACTIONS[action];
  const fields = [];
  const value = { notify: true, shiftDays: 1, day: addDays(todayIst(), 7), clock: '23:59' };
  if (action === 'postpone') {
    fields.push({ key: 'shiftDays', label: 'Kaç gün kaysın?', type: 'number', min: -30, max: 60, required: true,
      hint: 'Eksi değer öne alır (en çok 30 gün); en çok 60 gün ertelenir.' });
  }
  if (action === 'set_due') {
    fields.push({ key: 'day', label: 'Yeni son teslim günü', type: 'date', required: true },
      { key: 'clock', label: 'Saat', maxLength: 5, placeholder: '23:59', required: true,
        validate: (text) => (CLOCK.test(String(text || '').trim()) ? null : 'Saati 18:30 gibi yazın.') });
  }
  if (action !== 'delete' && action !== 'set_due') {
    fields.push({ key: 'notify', label: 'Öğrencilere tek özet bildirim gönder', type: 'checkbox' });
  }
  const description = {
    postpone: `${what}: teslim tarihi seçtiğiniz gün kadar kayar.`,
    set_due: `${what}: hepsine aynı son teslim tarihi verilir.`,
    cancel: `${what}: öğrenci listesinden kalkar; teslimler saklanır.`,
    delete: `${what} kalıcı olarak silinir; teslim fotoğrafları ayrılır. Geri alma yoktur.`,
  }[action];
  const result = await formDialog(app, {
    title: spec.title,
    description,
    value,
    fields,
    danger: action === 'delete' || action === 'cancel',
    submitLabel: spec.submit,
    onSubmit: (draft, { key }) => {
      const body = { ...scope, action };
      if (action === 'postpone') body.shiftDays = Number(draft.shiftDays);
      if (action === 'set_due') {
        const stamp = istanbulStamp(draft.day, String(draft.clock || '').trim());
        if (new Date(stamp).getTime() < Date.now()) throw new Error('Son teslim geçmiş bir zaman olamaz.');
        body.dueAt = stamp;
      }
      if ('notify' in draft && action !== 'delete' && action !== 'set_due') body.notify = Boolean(draft.notify);
      return app.api.post(`${HOMEWORK}/bulk-actions`, body, { key });
    },
  });
  if (!result || result === true) return null;
  const skipped = (result.skipped || []).length;
  app.toast(`${num(result.affectedCount)} ödev ${spec.done}${skipped ? ` · ${num(skipped)} atlandı (bulunamadı ya da `
    + 'zaten iptal)' : ''}.`, skipped ? 'warn' : 'good');
  return result;
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
  // Ders / konu / kaynak / açıklama alanları ortak (toplu ödev ve şablon formu da kullanır).
  // Program seçicinin seviye önerisi bu formdaki hedeften (sınıf / grup / öğrenci) gelir.
  const pieces = homeworkContent(app, current, subjects, { target: () => ({
    classGroupIds: target.classGroupIds, studentGroupIds: target.studentGroupIds,
    studentIds: [...target.students.keys()] }) });
  disposers.push(() => pieces.dispose());
  const { subjectOptions, topicField, resourceField, descriptionField } = pieces;

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
        validate: pieces.resourceValidate },
      { key: 'description', label: 'Açıklama', type: 'custom', render: descriptionField },
      { key: 'notify', label: 'Bildirim', type: 'custom', render: notifyField },
    ],
    submitLabel: current ? 'Kaydet' : 'Ödevi ver',
    onSubmit: (draft, { key }) => {
      const clock = clockControl?.value && CLOCK.test(clockControl.value) ? clockControl.value : due.clock;
      const body = {
        ...pieces.content(draft),
        target: {
          classGroupIds: target.classGroupIds,
          studentGroupIds: target.studentGroupIds,
          studentIds: [...target.students.keys()],
        },
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
