// BBD Akademi — Koçluk → Koçlar (F-K2 toplu atama, F-K3 profil, F-K21 metrikler, F-K22 dışa aktarım).
//
// ÖĞRETMEN = KOÇ: liste `admin_listCoaches`'tır (Öğretmen / Koç rolündeki
// bütün etkin personel). Yük tablosu: öğrenci / kapasite (aşan kırmızı),
// bekleyen kontrol, açık soru, ilk yanıt medyanı, yayımlanan hafta, öğrenci
// uyumu, son 30 günde görüşme. Satır koç çekmecesini açar:
//   · Profil (`admin_getCoachProfile` / `admin_putCoachProfile`, yalnız KM):
//     görünen ad, branşlar, kısa tanıtım, mesaj saatleri, teslim bildirimi
//     özeti, görüşme talebini otomatik onay.
//   · Performans (`admin_getCoachMetrics`): tarih aralığında kontrol, soru
//     yanıtı, SLA aşımı, yayımlanan hafta, öğrenci uyumu, görüşme, yoklama
//     zamanında alınma. Yalnız Akademi verisi.
// "Toplu koç ata" (`admin_bulkAssignCoach`, yalnız KM): sınıf / grup / seviye /
// öğrenci → önizleme (atanacak · değişecek · atlanacak, kapasite uyarısı) →
// uygula. Koçu olan öğrenci varsayılan olarak atlanır; değiştirilecekse
// gerekçe zorunlu (öğrenci ve veliye "koçun değişti" bildirimi gider).
// "Dışa aktar" (`admin_exportCoaching`): koç performansı, dönem raporları,
// haftalık programlar — ortak dışa aktarım işi olarak iner.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow, hintBox, kpiRow, tabBar } from '../../../ui-kit/layout.js';
import {
  GRADE_LEVELS, actionButton, dateInput, errorBox, exportJob, fmtDate, fmtDateTime, formDialog, gradeText, kv,
  loader, multiChips, openDrawer, optionsOf, pagedList, searchBox, selectBox, staticList, statusBadge, todayIst, toolbar,
} from '../common.js';
import { loadTargets, targetsField } from '../plan-tasks.js';
import {
  BULK_ACTIONS, EXPORT_FORMATS, EXPORT_KINDS, SUBMISSION_DIGEST, bulkAssignBody, exportBody, hoursText, pct,
  profilePut,
} from './coaching-logic.js';
import { COACHES, coachOptions, setEnabled, studentCell } from './coaching-shared.js';

const SORTS = { name: 'Ada göre', load: 'Yük (öğrenci sayısı)', pending_reviews: 'Bekleyen kontrol',
  response_time: 'Yanıt süresi' };

export function tab(app, ctx) {
  const node = h('div', 'ak-stack');
  const views = [{ key: 'coaches', label: 'Koçlar' },
    app.can('admin_listCoachRoster') && { key: 'roster', label: 'Eşleme tablosu' }].filter(Boolean);
  let view = 'coaches';
  const coachesView = h('div', 'ak-stack');
  let roster = null;
  const chips = chipRow(views, view, (key) => {
    view = key || view;
    chips.set(view);
    coachesView.hidden = view !== 'coaches';
    if (view === 'roster') {
      roster = roster || rosterView(app, ctx);
      if (!roster.node.parentNode) node.append(roster.node);
      roster.node.hidden = false;
      roster.start();
    } else if (roster) {
      roster.node.hidden = true;
    }
  });
  if (views.length > 1) node.append(chips.node);
  node.append(coachesView);
  let sort = 'name';
  const sortBox = selectBox(optionsOf(SORTS), sort, (value) => { sort = value || 'name'; list.reload(); }, { label: 'Sıra' });
  const assign = actionButton(app, 'admin_bulkAssignCoach', 'Toplu koç ata', { variant: 'primary',
    onClick: async () => { if (await bulkAssignDialog(app)) refresh(); } });
  coachesView.append(toolbar(sortBox.node, h('span', 'kit-spacer'), assign),
    hintBox('Öğretmen = koç: Öğretmen / Koç rolündeki bütün etkin personel burada listelenir. Tek öğrencinin koçu '
      + 'öğrenci kartındaki Koçluk sekmesinden değiştirilir.'));

  const list = staticList({
    load: () => app.api.getCached(COACHES, { sort }),
    onRow: (row) => openCoach(app, row, ctx, () => refresh()),
    rowKey: (row) => row.coach?.id,
    columns: [
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.coach?.displayName || '—'),
          h('small', '', (row.coach?.subjects || []).map((subject) => subject.shortName || subject.name).join(', ')));
        return cell;
      } },
      { key: 'students', label: 'Öğrenci', width: '120px', cell: (row) => loadBadge(row) },
      { key: 'pending', label: 'Bekleyen', width: '150px', cell: (row) => `${row.pendingReviews ?? 0} kontrol · ${row.openQuestions ?? 0} soru` },
      { key: 'response', label: 'İlk yanıt (medyan)', width: '140px', cell: (row) => hoursText(row.medianFirstResponseHours) },
      { key: 'weeks', label: 'Yayımlanan hafta', width: '130px', align: 'num', cell: (row) => pct(row.weeksPublishedPercent) },
      { key: 'adherence', label: 'Öğrenci uyumu', width: '120px', align: 'num', cell: (row) => pct(row.studentAdherencePercent) },
      { key: 'meetings', label: 'Görüşme (30 gün)', width: '130px', align: 'num', cell: (row) => String(row.meetings30d ?? 0) },
    ],
    empty: { title: 'Koç yok', text: 'Personel ekranında öğretmenlere "Öğretmen / Koç" rolü verildiğinde burada görünür.' },
  });
  coachesView.append(list.node);
  const refresh = () => { app.api.invalidate(COACHES); list.reload(); roster?.reload(); };
  let started = false;
  return { node, shown() { if (!started) { started = true; list.reload(); } }, destroy() { roster?.destroy(); } };
}

// ------------------------------------------------------------ eşleme tablosu

const ROSTER_STATUS = { scheduled: ['Başlayacak', 'info'], active: ['Etkin', 'good'], ended: ['Bitti', 'dim'] };

/** Öğrenci × koç eşleme tablosu (`admin_listCoachRoster`): koçsuzları bulup toplu atamaya götürür. */
function rosterView(app, ctx) {
  const node = h('div', 'ak-stack');
  const filter = { q: '', classGroupId: '', coachId: '', coach: '' };
  const search = searchBox({ placeholder: 'Öğrenci ara (ad, okul no)', label: 'Öğrenci ara',
    onSearch: (text) => { filter.q = text; list.reload(); } });
  const classSlot = h('div');
  const coachSlot = h('div');
  const state = selectBox([{ value: '', label: 'Hepsi' }, { value: 'assigned', label: 'Koçu olanlar' },
    { value: 'none', label: 'Koçu olmayanlar' }], '', (value) => { filter.coach = value; list.reload(); }, { label: 'Koç durumu' });
  const assign = actionButton(app, 'admin_bulkAssignCoach', 'Seçilenlere koç ata', { variant: 'primary', onClick: async () => {
    const students = list.table.selectedRows().map((row) => row.student).filter(Boolean);
    if (await bulkAssignDialog(app, { students })) { app.api.invalidate(COACHES); list.reload(); }
  } });
  setEnabled(assign, false);
  node.append(toolbar(search.node, classSlot, coachSlot, state.node, h('span', 'kit-spacer'), assign));
  app.lookups.options('classGroups', { empty: 'Bütün sınıflar' }).then((options) => {
    classSlot.append(selectBox(options, '', (value) => { filter.classGroupId = value; list.reload(); }, { label: 'Sınıf' }).node);
  }).catch(() => {});
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachSlot.append(selectBox(options, '', (value) => { filter.coachId = value; list.reload(); }, { label: 'Koç' }).node);
  });
  const list = pagedList({
    app,
    path: '/v1/admin/coach-assignments/roster',
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    selectable: true,
    rowKey: (row) => row.student?.id,
    onSelect: (keys) => setEnabled(assign, keys.length > 0),
    onRow: (row) => ctx.openStudent?.(row.student),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => studentCell(row.student) },
      { key: 'coach', label: 'Koç', width: 'minmax(0, 1fr)', cell: (row) => (row.assignment?.coach?.displayName
        || badge('Koçu yok', 'warn')) },
      { key: 'since', label: 'Başlangıç', width: '120px', cell: (row) => (row.assignment ? fmtDate(row.assignment.startsOn) : '—') },
      { key: 'status', label: 'Durum', width: '120px', cell: (row) => (row.assignment
        ? statusBadge(ROSTER_STATUS, row.assignment.status) : '—') },
    ],
    empty: { title: 'Eşleşen öğrenci yok', text: 'Süzgeci değiştirin.' },
  });
  node.append(list.node);
  let started = false;
  return { node, start() { if (!started) { started = true; list.reload(); } }, reload: () => started && list.reload(),
    destroy() { list.destroy(); search.destroy(); } };
}

/** Öğrenci / kapasite rozeti (aşan kırmızı). */
function loadBadge(row) {
  const count = row.studentCount ?? 0;
  if (row.maxStudents === null || row.maxStudents === undefined) return badge(String(count), count ? '' : 'dim');
  return badge(`${count} / ${row.maxStudents}`, count > row.maxStudents ? 'bad' : count === row.maxStudents ? 'warn' : '');
}

// ------------------------------------------------------------- koç çekmecesi

function openCoach(app, row, ctx, onChange) {
  const staffId = row.coach?.id;
  if (!staffId) return;
  const view = openDrawer(app, { title: row.coach.displayName || 'Koç', subtitle: 'Koç profili ve performans' });
  const tabs = [
    app.can('admin_getCoachProfile') && { key: 'profile', label: 'Profil' },
    app.can('admin_getCoachMetrics') && { key: 'metrics', label: 'Performans' },
  ].filter(Boolean);
  if (!tabs.length) {
    view.body.replaceChildren(alertBox('Koç profili için yetkiniz yok.', 'info'));
    return;
  }
  const slot = h('div', 'ak-stack');
  const panes = new Map();
  const show = (key) => {
    for (const [name, pane] of panes) pane.node.hidden = name !== key;
    if (panes.has(key)) return;
    const pane = key === 'metrics' ? metricsPane(app, staffId) : profilePane(app, staffId, view, onChange);
    panes.set(key, pane);
    slot.append(pane.node);
    pane.reload();
  };
  const bar = tabBar(tabs, tabs[0].key, show);
  const tools = h('div', 'ak-row-actions');
  if (app.can('admin_getCoachQueue')) {
    tools.append(button('Koçun kuyruğu', { variant: 'ghost', onClick: () => { view.close(); ctx.go?.('queue', { coachId: staffId }); } }));
  }
  view.body.replaceChildren(bar.node, slot, tools);
  show(tabs[0].key);
}

function profilePane(app, staffId, view, onChange) {
  const path = `/v1/admin/coaching/coaches/${staffId}/profile`;
  let profile = null;
  const pane = loader(() => app.api.get(path), (data) => {
    profile = data;
    view.setTitle(data.displayName || 'Koç');
    const capacity = data.maxStudents === null || data.maxStudents === undefined
      ? `${data.studentCount ?? 0} öğrenci (kurum kapasitesi)` : `${data.studentCount ?? 0} / ${data.maxStudents} öğrenci`;
    return [
      kv([
        ['Görünen ad', data.displayName || '—'],
        ['Branşlar', (data.subjects || []).map((subject) => subject.name).join(', ') || '—'],
        ['Tanıtım', data.bio || '—'],
        ['Mesaj saatleri', data.messageHours ? `${data.messageHours.start} – ${data.messageHours.end}` : 'Sınırsız'],
        ['Teslim bildirimi', SUBMISSION_DIGEST[data.submissionDigest] || data.submissionDigest || '—'],
        ['Görüşme talebi', data.meetingAutoConfirm ? 'Otomatik onaylanır' : 'Koç onaylar'],
        ['Yük', capacity],
        ['Son değişiklik', data.updatedAt ? fmtDateTime(data.updatedAt) : '—'],
      ]),
      toolbar(actionButton(app, 'admin_putCoachProfile', 'Profili düzenle', { variant: 'ghost', onClick: () => edit() })),
    ];
  }, { rows: 6, columns: 2 });

  async function edit() {
    if (!profile) return;
    const subjects = await app.lookups.options('subjects').catch(() => []);
    const saved = await formDialog(app, {
      title: 'Koç profili',
      description: 'Öğrenci ve veli koçluk ekranında görünen ad, branş ve tanıtım; mesaj saatleri dışında gelen mesaj '
        + 'koça ertesi sabah bildirilir.',
      fields: [
        { key: 'displayName', label: 'Görünen ad', maxLength: 80, wide: true },
        { key: 'subjectIds', label: 'Branşlar (en çok 10)', type: 'custom', render: (draft, set) => multiChips(subjects,
          draft.subjectIds || [], (value) => set(value)).node },
        { key: 'bio', label: 'Kısa tanıtım', type: 'textarea', maxLength: 500, wide: true },
        { key: 'messageStart', label: 'Mesaj saati başlangıç (SS:DD)', maxLength: 5 },
        { key: 'messageEnd', label: 'Mesaj saati bitiş (SS:DD)', maxLength: 5 },
        { key: 'submissionDigest', label: 'Teslim bildirimi', type: 'select', options: optionsOf(SUBMISSION_DIGEST) },
        { key: 'meetingAutoConfirm', label: 'Görüşme talebini otomatik onayla', type: 'checkbox' },
      ],
      value: {
        displayName: profile.displayName || '',
        subjectIds: (profile.subjects || []).map((subject) => subject.id),
        bio: profile.bio || '',
        messageStart: profile.messageHours?.start || '',
        messageEnd: profile.messageHours?.end || '',
        submissionDigest: profile.submissionDigest || 'instant',
        meetingAutoConfirm: Boolean(profile.meetingAutoConfirm),
      },
      onSubmit: (draft) => {
        const clock = /^([01]\d|2[0-3]):[0-5]\d$/;
        for (const value of [draft.messageStart, draft.messageEnd]) {
          if (value && !clock.test(value)) throw new Error('Mesaj saatini SS:DD biçiminde yazın.');
        }
        if (Boolean(draft.messageStart) !== Boolean(draft.messageEnd)) {
          throw new Error('Mesaj saatinin başlangıcını ve bitişini birlikte yazın (ya da ikisini de boş bırakın).');
        }
        const built = profilePut(profile, draft);
        if (built.error) throw new Error(built.error);
        return app.api.put(path, built.body);
      },
    });
    if (!saved) return;
    app.toast('Koç profili kaydedildi.', 'good');
    onChange?.();
    pane.reload();
  }
  return pane;
}

function metricsPane(app, staffId) {
  const node = h('div', 'ak-stack');
  const range = { from: todayIst(-30), to: todayIst(0) };
  const from = dateInput(range.from, (value) => { range.from = value || range.from; view.reload(); }, 'Başlangıç');
  const to = dateInput(range.to, (value) => { range.to = value || range.to; view.reload(); }, 'Bitiş');
  const view = loader(() => app.api.get(`/v1/admin/coaching/coaches/${staffId}/metrics`, { from: range.from, to: range.to }),
    (data) => [
      kpiRow([
        { label: 'Öğrenci', value: String(data.studentCount ?? 0) },
        { label: 'Kontrol', value: String(data.reviewsDone ?? 0), title: `Kontrol medyanı ${hoursText(data.medianReviewHours)}` },
        { label: 'Yanıtlanan soru', value: String(data.questionsAnswered ?? 0) },
        { label: 'SLA aşımı', value: String(data.slaBreaches ?? 0), tone: data.slaBreaches ? 'bad' : '' },
        { label: 'Görüşme', value: String(data.meetingsHeld ?? 0) },
      ]),
      kv([
        ['Dönem', `${fmtDate(data.from)} – ${fmtDate(data.to)}`],
        ['Kontrol süresi (medyan)', hoursText(data.medianReviewHours)],
        ['Soruya ilk yanıt (medyan)', hoursText(data.medianFirstResponseHours)],
        ['Yayımlanan hafta', pct(data.weeksPublishedPercent)],
        ['Öğrenci plan uyumu', pct(data.studentAdherencePercent)],
        ['Yoklamayı zamanında alma', pct(data.attendanceOnTimePercent)],
      ]),
      h('small', 'ak-muted', 'Yalnız Akademi kayıtlarından hesaplanır (kontrol, soru, plan, görüşme, yoklama).'),
    ], { rows: 4, columns: 5 });
  node.append(toolbar(from.node, to.node), view.node);
  return { node, reload: view.reload };
}

// ----------------------------------------------------------- toplu koç atama

/** Toplu koç atama: form → önizleme (dryRun) → uygula. Dönüş: uygulanan sonuç | null. */
export async function bulkAssignDialog(app, { students = [] } = {}) {
  const [coaches, [classes, groups]] = await Promise.all([coachOptions(app), loadTargets(app)]);
  if (!coaches.length) {
    app.toast('Koç yok: önce personele "Öğretmen / Koç" rolü verin.', 'bad');
    return null;
  }
  let target = { classGroupIds: [], studentGroupIds: [], studentIds: students.map((student) => student.id) };
  const form = await formDialog(app, {
    title: 'Toplu koç ata',
    description: 'Seçilen sınıf, grup, seviye ve öğrencilere aynı koç atanır. Önce önizleme gösterilir; kayıt '
      + '"Uygula" ile yapılır.',
    wide: true,
    fields: [
      { key: 'coachId', label: 'Koç', type: 'select', required: true, options: [{ value: '', label: 'Koç seçin' }, ...coaches] },
      { key: 'target', label: 'Kime', type: 'custom', render: () => {
        const field = targetsField(app, { classes, groups, value: { students }, onChange: (value) => { target = value; } });
        return field.node;
      } },
      { key: 'gradeLevels', label: 'Seviye (isteğe bağlı)', type: 'custom', render: (draft, set) => multiChips(
        GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) })), draft.gradeLevels || [],
        (value) => set(value)).node },
      { key: 'startsOn', label: 'Başlangıç', type: 'date' },
      { key: 'onExisting', label: 'Koçu olan öğrenci', type: 'select',
        options: [{ value: 'skip', label: 'Atla (koçu kalsın)' }, { value: 'change', label: 'Koçunu değiştir' }] },
      { key: 'changeReason', label: 'Değişiklik gerekçesi (koçu değişenler için zorunlu)', maxLength: 300, wide: true },
      { key: 'handoverNote', label: 'Devir notu (yeni koç görür)', type: 'textarea', maxLength: 2000, wide: true },
    ],
    value: { coachId: '', gradeLevels: [], startsOn: todayIst(0), onExisting: 'skip', changeReason: '', handoverNote: '' },
    submitLabel: 'Önizle',
    onSubmit: async (draft, { key }) => {
      const built = bulkAssignBody({ ...target, ...draft }, { dryRun: true });
      if (built.error) throw new Error(built.error);
      const preview = await app.api.post('/v1/admin/coach-assignments/bulk', built.body, { key });
      return { draft, preview };
    },
  });
  if (!form) return null;
  return previewDialog(app, form.preview, { ...target, ...form.draft });
}

function previewDialog(app, preview, input) {
  return new Promise((resolve) => {
    const view = openDrawer(app, { title: 'Toplu koç atama — önizleme', subtitle: preview.capacity?.coach?.displayName || '',
      onClose: () => resolve(null) });
    const parts = [kpiRow([
      { label: 'Atanacak', value: String(preview.assignCount ?? 0), tone: preview.assignCount ? 'good' : '' },
      { label: 'Koçu değişecek', value: String(preview.changeCount ?? 0), tone: preview.changeCount ? 'warn' : '' },
      { label: 'Atlanacak', value: String(preview.skipCount ?? 0) },
    ])];
    const capacity = preview.capacity;
    if (capacity?.exceeded) {
      parts.push(alertBox(`Kapasite aşıldı: ${capacity.coach?.displayName || 'koç'} atamadan sonra ${capacity.studentCount} `
        + `öğrenciye çıkar (sınır ${capacity.maxStudents}). Uyarıdır, engel değil.`, 'warn'));
    }
    const rows = h('div', 'ak-stack');
    const table = staticList({
      load: async () => preview.rows || [],
      columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => studentCell(row.student) },
        { key: 'current', label: 'Şimdiki koç', width: 'minmax(0, 1fr)', cell: (row) => row.currentCoach?.displayName || '—' },
        { key: 'action', label: 'Sonuç', width: '170px', cell: (row) => statusBadge(BULK_ACTIONS, row.action) },
      ],
      empty: { title: 'Hedefte öğrenci yok', text: 'Seçimi genişletin.' },
    });
    rows.append(table.node);
    table.reload();
    parts.push(rows);
    const actionable = (preview.assignCount ?? 0) + (preview.changeCount ?? 0);
    const apply = button('Uygula', { variant: 'primary', disabled: !actionable, onClick: async () => {
      apply.disabled = true;
      try {
        const built = bulkAssignBody(input, { dryRun: false });
        if (built.error) throw new Error(built.error);
        const result = await app.api.post('/v1/admin/coach-assignments/bulk', built.body);
        app.toast(`${result.assignCount ?? 0} öğrenciye koç atandı${result.changeCount ? `, ${result.changeCount} öğrencinin koçu değişti` : ''}.`,
          'good');
        view.close();
        resolve(result);
      } catch (failure) {
        apply.disabled = false;
        rows.prepend(errorBox(failure));
      }
    } });
    parts.push(toolbar(button('Vazgeç', { variant: 'ghost', onClick: () => view.close() }), apply));
    view.body.replaceChildren(...parts);
  });
}

// --------------------------------------------------------------- dışa aktarım

/** Koçluk dışa aktarımı (başlık düğmesi). */
export async function exportDialog(app) {
  const [coaches, terms] = await Promise.all([coachOptions(app), app.lookups.options('terms').catch(() => [])]);
  const form = await formDialog(app, {
    title: 'Koçluk dışa aktarımı',
    description: 'Dosya arka planda hazırlanır ve hazır olunca iner.',
    fields: [
      { key: 'kind', label: 'Ne', type: 'select', required: true,
        options: Object.entries(EXPORT_KINDS).map(([value, [label]]) => ({ value, label })) },
      { key: 'format', label: 'Biçim', type: 'select', required: true, options: optionsOf(EXPORT_FORMATS) },
      { key: 'from', label: 'Başlangıç', type: 'date', required: true },
      { key: 'to', label: 'Bitiş', type: 'date', required: true },
      { key: 'termId', label: 'Dönem (dönem raporları için)', type: 'select',
        options: [{ value: '', label: terms.length ? 'Dönem seçin' : 'Dönem tanımlı değil' }, ...terms] },
      { key: 'coachIds', label: 'Koçlar (boş = hepsi)', type: 'custom', render: (draft, set) => multiChips(coaches,
        draft.coachIds || [], (value) => set(value)).node },
    ],
    value: { kind: 'coach_performance', format: 'xlsx', from: todayIst(-30), to: todayIst(0), termId: '', coachIds: [] },
    submitLabel: 'Hazırla',
    onSubmit: async (draft) => {
      const built = exportBody(draft);
      if (built.error) throw new Error(built.error);
      return built.body;
    },
  });
  if (!form) return null;
  return exportJob(app, () => app.api.post('/v1/admin/coaching/exports', form),
    { name: `kocluk-${form.kind}`, label: 'Koçluk dosyası hazırlanıyor…' });
}

