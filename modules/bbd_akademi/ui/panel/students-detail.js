// BBD Akademi — Öğrenci detayı (F-A2): başlık ve öğrenim sekmeleri.
//
// Çekmecenin kabuğu (sekme kaydı, tembel yükleme) `students.js` → `openStudent`
// içindedir; buradaki sekmeler Özet, Program, Müfredat & ilerleme ve Kaynaklar,
// kalanlar `students-records.js` içindedir. Bu dosya ayrıca iki dosyanın
// paylaştığı etiket ve küçük çizim yardımcılarını dışa verir.
//
// TEMBEL VE PARALEL. Her sekme ilk açıldığında kurulur; bir sekmenin birbirinden
// bağımsız okumaları aynı anda gider (her kartın kendi yükleyicisi var; biri
// düşerse ötekiler çizilir). Satır başına ek istek atılmaz: ayrıntı yalnız
// satıra tıklanınca açılır. Okumalar salt-okunur öğrenci uçlarıdır
// (`/v1/students/{id}/*`); KM entegrasyon anahtarına izin listesinde açıktır.
//
// SEVİYE / ALAN / HEDEF SINAV BURADA DÜZENLENMEZ: KM'nin Öğrenciler modülünde
// (`bbd_students`) tanımlanır, Akademi aynasıdır (A5). Düğme o ekranı açar.
//
// İKİ PARA AYRI (A12): Özet sekmesi kurum açık bakiyesini
// (`institutionOpenBalanceMinor`) ve kantin harcamasını (`canteenSpentTodayMinor`)
// BİLEREK göstermez; ikisi kendi sekmelerinde, ayrı renk çizgisiyle durur.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { lineChart } from '../../ui-kit/charts.js';
import {
  DAY_LABEL, EXAM_TARGETS, TRACKS, actionButton, addDays, confirmAction, empty, fmtDate,
  fmtDateTime, fmtTime, formDialog, gradeText, kv, labelOf, loader, money, net, num, openDrawer,
  optionsOf, pagedList, percent, personName, runAction, selectBox, statusBadge, todayIst, toolbar,
  weekStart,
} from './common.js';
import { goalDialog } from './coaching-students.js';
import { EXAM_TYPES } from './exams-shared.js';
import { documentButtons } from './documents.js';
import { studentCurriculumSet } from './curriculum-set.js';
import { classHistoryCard } from './students-class-history.js';
import { canteenDoc, homeworkTally, statementDoc, studentCardDoc, studentHomeworkDoc } from './documents-logic.js';

// ------------------------------------------------------------- etiketler

export const STUDENT_STATUS = { active: ['Aktif', 'good'], inactive: ['Pasif (KM)', 'dim'],
  missing: ['KM\'de bulunamadı', 'warn'] };
/** KM pasifleştirme gerekçesi (sözleşme `StudentStatusReason`). */
export const STATUS_REASONS = { left: 'Ayrıldı', graduated: 'Mezun', suspended: 'Geçici durduruldu',
  other: 'Diğer' };
/** Deneme türü adı tek tablodan (`exams-shared.js`; Akademi V17). */
export { EXAM_TYPES };
export const ATTENDANCE = { present: ['Geldi', 'good'], absent: ['Gelmedi', 'bad'], late: ['Geç kaldı', 'warn'],
  excused: ['İzinli', 'info'] };
export const HOMEWORK_STATUS = { pending: ['Bekliyor', 'info'], completed: ['Teslim edildi', 'good'],
  overdue: ['Gecikti', 'bad'] };
const ITEM_STATUS = { scheduled: ['Planlı', 'info'], cancelled: ['İptal', 'dim'], changed: ['Değişti', 'warn'] };
const ITEM_KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik', break: 'Teneffüs' };
const CHANGE_KIND = { added: 'eklendi', cancelled: 'iptal', moved: 'taşındı', teacher_changed: 'öğretmen değişti',
  room_changed: 'derslik değişti' };
const TOPIC_STATUS = { not_started: ['Başlanmadı', 'dim'], in_progress: ['Çalışılıyor', 'info'],
  done: ['Tamamlandı', 'good'], review: ['Tekrarda', 'warn'] };
const PROGRAM_KIND = { school: 'Okul', exam: 'Sınav', custom: 'Kuruma özel' };
const PROGRAM_SOURCE = { rule: 'Kurala göre', class_override: 'Sınıfa özel', student_override: 'Öğrenciye özel' };
const CURRICULUM_WARNINGS = {
  grade_level_missing: 'Sınıf seviyesi KM\'de boş; okul müfredatı seçilemiyor.',
  track_missing: 'Alan (SAY / EA / SÖZ / DİL) KM\'de boş; alan programları seçilemiyor.',
  exam_target_missing: 'Hedef sınav KM\'de boş; sınav programları seçilemiyor.',
};
const REVIEW_REASON = { schedule: 'Takvim', weak_exam: 'Denemede zayıf', manual: 'Elle eklendi' };
const RESOURCE_KIND = { topic_book: 'Konu anlatımı', question_bank: 'Soru bankası', fascicle: 'Fasikül',
  practice_exam: 'Deneme', other: 'Diğer' };
const SHELF_SOURCE = { assigned: 'Atanan', package: 'Paketten', self: 'Öğrencinin eklediği' };
const SECTION_KIND = { test: 'Test', chapter: 'Bölüm', unit_test: 'Ünite testi', review: 'Tekrar', other: 'Diğer' };
const SIGNALS = { attendance: 'yoklama', canteen: 'kantin', tablet: 'tablet' };
const PHONE_FIELDS = { studentPhone: 'öğrenci telefonu', guardian1Phone: '1. veli telefonu',
  guardian2Phone: '2. veli telefonu', guardian3Phone: '3. veli telefonu', guardian4Phone: '4. veli telefonu',
  parentPhone: '1. veli telefonu', parentPhone2: '2. veli telefonu' };
// Veli yakınlığı (Akademi `GuardianRelation`, KM'den; veli-senkron-4).
const RELATIONS = { mother: 'Anne', father: 'Baba', guardian: 'Vasi', grandparent: 'Büyükanne / büyükbaba',
  sibling: 'Kardeş', other: 'Diğer' };
export const guardianRole = (row) => [RELATIONS[row.relation] || `${row.slot}. veli`,
  row.primary ? 'birincil' : ''].filter(Boolean).join(' · ');

// ---------------------------------------------------------- küçük yardımcılar

export const isSet = (value) => value !== null && value !== undefined && value !== '';
export const minutesText = (value) => {
  const minutes = Math.round(Number(value) || 0);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} sa ${minutes % 60} dk` : `${minutes} dk`;
};
export const secondsText = (value) => minutesText((Number(value) || 0) / 60);
/** Zaman damgasının Europe/Istanbul günü → '12.10.2026'. */
export const dayOf = (stamp) => (stamp ? fmtDateTime(stamp).slice(0, 10) : '—');
export const shortDate = (iso) => fmtDate(iso).slice(0, 5);
export const dqb = (row) => `${num(row.correct)} D · ${num(row.wrong)} Y · ${num(row.blank)} B`;
const change = (current, previous, title) => (previous
  ? { percent: Math.round(((Number(current) - previous) / previous) * 100), title } : null);

/** Ana metin + sönük alt satır. */
export function stack(main, sub) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', isSet(main) ? String(main) : '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
}

/** Satır içi bağlantı düğmesi — satır tıklamasını yemez. */
export function linkButton(label, onClick, title = '') {
  const node = h('button', 'ak-link', label);
  node.type = 'button';
  if (title) node.title = title;
  node.addEventListener('click', (event) => { event.stopPropagation(); onClick(); });
  return node;
}

export function checkbox(label, onChange, checked = false) {
  const wrap = h('label', 'ak-inline ak-stu-check');
  const input = h('input', 'kit-check');
  input.type = 'checkbox';
  input.checked = checked;
  input.addEventListener('change', () => onChange(input.checked));
  wrap.append(input, h('span', '', label));
  return wrap;
}

/** Başlık şeridine araç konabilen kart. */
export function panelCard(title, content, tools = []) {
  const box = card(title, content);
  const live = tools.filter(Boolean);
  const head = box.querySelector('.kit-card-head');
  if (head && live.length) {
    const slot = h('div', 'ak-stu-card-tools');
    slot.append(...live);
    head.append(slot);
  }
  return box;
}

export function padded(...nodes) {
  const box = h('div', 'kit-card-body ak-stack');
  box.append(...nodes.filter(Boolean));
  return box;
}

export function grid2(left, right) {
  const node = h('div', 'ak-grid-2');
  node.append(left, right);
  return node;
}

/** Sıkı tablo (liste yükündeki dizi için; sayfalı listeler `pagedList`). */
export function table(columns, rows, emptyTitle, extra = {}) {
  return dataTable({ columns, rows: rows || [], dense: true, empty: empty(emptyTitle), ...extra }).node;
}

/** Öğrenci çekmecesinin üstüne açılan dar ayrıntı çekmecesi. */
export function subDrawer(ctx, options) {
  const panel = openDrawer(ctx.app, options);
  panel.node.classList.add('ak-stu-sub');
  return panel;
}

export function subjectLabel(ctx, id) {
  return ctx.subjectNames.get(id) || id || '—';
}

function bar(value) {
  const track = h('span', 'ak-stu-bar');
  const fill = h('span', 'ak-stu-bar-fill');
  fill.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
  track.append(fill);
  return track;
}

function heat(level) {
  const value = Math.max(0, Math.min(4, Number(level) || 0));
  const node = h('span', `ak-stu-heat ak-stu-heat-${value}`, String(value));
  node.title = `Isı ${value}/4`;
  return node;
}

function resourceMeta(resource) {
  return [RESOURCE_KIND[resource?.kind], resource?.publisher, resource?.year].filter(Boolean).join(' · ');
}

function itemText(item) {
  if (!item) return '';
  return [`${fmtTime(item.startsAt)}–${fmtTime(item.endsAt)}`, item.subject?.name || ITEM_KIND[item.kind],
    item.teacher?.displayName, item.room].filter(Boolean).join(' · ');
}

/**
 * Öğrencinin KM kaydına git (seviye/alan/hedef, sınıf, veliler, kantin ayarları).
 * Öğrenci yönetimi BBD Akademi'de (13.10.2026): kart içindeyse "Kayıt ve veliler"
 * sekmesi seçilir; kart dışındaysa Öğrenciler → yönetim listesi açılır. Öğrenci
 * yönetimi yeteneği yoksa (modül kapalı / izin yok) eski davranış: KM Öğrenci
 * Yönetimi ekranı.
 */
export function openKmStudents(app, student, extra = {}, ctx = null) {
  if (ctx?.recordable) {
    ctx.select('record');
    return;
  }
  if (app.capability?.('bbd_students.manager')) {
    if (student?.externalRef) openKmById(app, student.externalRef);
    else app.go('students', { view: 'km' });
    return;
  }
  app.openPanel('bbd_students', student?.id
    ? { studentId: student.id, externalRef: student.externalRef, ...extra } : null);
}

/** Kart dışından KM kimliğiyle tek kartı aç (`students.js` → `openStudentByRef`). */
function openKmById(app, kmId) {
  app.go('students', { kmStudentId: kmId });
}

/** KM'de yönetilen alanların notu: kart içinde sekmeye, yoksa KM ekranına götürür. */
export function kmManagedHint(ctx, text, student, extra = {}) {
  const hint = h('div', 'kit-hint ak-stu-hint');
  hint.append(h('span', '', ctx.recordable ? `${text} “Kayıt ve veliler” sekmesinde düzenlenir.`
    : `${text} KM'nin Öğrenci Yönetimi ekranında düzenlenir.`),
  button(ctx.recordable ? 'Kaydı düzenle' : 'Öğrenci Yönetimi\'nde aç',
    { onClick: () => openKmStudents(ctx.app, student, extra, ctx) }));
  return hint;
}

// ---------------------------------------------------------------- başlık

export function renderHeader(ctx, data) {
  const { app } = ctx;
  const student = data.student || {};
  const nodes = [];
  if (student.status === 'missing') {
    nodes.push(alertBox('Bu öğrenci son iki tam eşitlemede KM öğrenci dizininde görünmedi. Kaydı KM '
      + 'Öğrenciler modülünden kontrol edin.', 'warn'));
  }
  if (student.status === 'inactive') {
    const why = STATUS_REASONS[data.statusReason] || '';
    nodes.push(alertBox(`Pasif öğrenci${why ? ` — ${why}` : ''}${data.statusSince
      ? ` (${fmtDateTime(data.statusSince)})` : ''}. KM'de pasifleştirildi: öğrenci ve yalnız ona bağlı veli `
      + 'uygulamaya giremez, veriler saklanır. Bu ekran SALT OKUNURDUR; kurum cari tahsilatı açık kalır. '
      + 'Yeniden etkinleştirme KM Öğrenciler modülünden yapılır.', 'info'));
  }
  if ((data.phoneIssues || []).length) {
    nodes.push(alertBox(`KM'de okunamayan telefon: ${data.phoneIssues.map((key) => labelOf(PHONE_FIELDS, key))
      .join(', ')}. Bu numaralara giriş kodu ve bildirim gitmez; KM Öğrenciler modülünde düzeltin.`, 'warn'));
  }

  const level = h('span', 'ak-inline');
  level.append(h('span', '', gradeText(student.gradeLevel)));
  const suggestion = data.gradeLevelSuggestion;
  if (isSet(suggestion) && String(suggestion) !== String(student.gradeLevel ?? '')) {
    const hint = badge(`Öneri: ${gradeText(suggestion)}`, 'info');
    hint.title = 'KM\'de seviye boş; sınıf adından önerildi. Akademi bunu kaydetmez — KM\'de tanımlayın.';
    level.append(hint);
  }
  const classCell = student.classGroup
    ? linkButton(student.classGroup.name, () => app.go('students', { classGroupId: student.classGroup.id }),
      'Sınıfı aç')
    : '—';
  const identity = card('Kimlik', kv([
    ['Öğrenci no', student.externalRef],
    ['Okul no', data.schoolNo],
    ['Sınıf', classCell],
    ['Seviye', level],
    ['Alan', TRACKS[student.track] || '—'],
    ['Hedef sınav', EXAM_TARGETS[student.examTarget] || '—'],
    ['Öğrenci telefonu', data.studentPhoneMasked],
    ['Durum', statusBadge(STUDENT_STATUS, student.status)],
    student.status === 'inactive' ? ['Pasifleştirme gerekçesi', STATUS_REASONS[data.statusReason] || '—'] : null,
    ['Çalışma grupları', (data.studentGroups || []).map((group) => group.name).join(', ') || '—'],
    ['Son eşitleme', fmtDateTime(data.syncedAt)],
  ]));
  // KM'de yönetilir: seviye/alan/hedef sınav müfredatın kaynağıdır (A5).
  const readOnly = kmManagedHint(ctx, 'Seviye, alan ve hedef sınav müfredatı belirler; KM\'de yönetilir —',
    student);
  const left = h('div', 'ak-stack');
  left.append(identity, readOnly);
  nodes.push(grid2(left, guardianCard(ctx, data)));
  nodes.push(studentDocuments(ctx, data));

  const programs = dataTable({
    dense: true,
    columns: [
      { key: 'title', label: 'Program', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, row.subject?.name) },
      { key: 'kind', label: 'Tür', cell: (row) => [PROGRAM_KIND[row.kind], row.exam].filter(Boolean).join(' · ') },
      { key: 'grade', label: 'Seviye', cell: (row) => gradeText(row.grade) },
      { key: 'size', label: 'Kapsam', cell: (row) => (isSet(row.topicCount)
        ? `${num(row.unitCount)} ünite · ${num(row.topicCount)} konu` : '—') },
    ],
    rows: data.programs || [],
    onRow: (row) => ctx.select('curriculum', { programId: row.id }),
    empty: empty('Program atanmamış', 'Seviye, alan ve hedef sınav tanımlanınca müfredat kendiliğinden seçilir.'),
  });
  nodes.push(card('Programlar', programs.node, 'Satıra tıklayınca konu ilerlemesi açılır'));
  return nodes;
}

/**
 * Öğrencinin belgeleri: özet kartı (ekrandaki veriden, ek istek yok), ödevler,
 * kantin dökümü (son 30 gün) ve kurum cari ekstresi (eğitim yılı başından;
 * yalnız finans izniyle). Kantin ile kurum carisi AYRI belgelerdir (A12).
 */
function studentDocuments(ctx, data) {
  const { app, id } = ctx;
  const student = data.student || {};
  const today = todayIst();
  const year = Number(today.slice(0, 4)) - (Number(today.slice(5, 7)) < 9 ? 1 : 0);
  const items = [documentButtons(app, { title: 'Öğrenci özet kartı', label: 'Özet kartı',
    build: async () => studentCardDoc(data) })];
  if (app.can('students_listHomework')) {
    items.push(documentButtons(app, { title: 'Öğrenci ödev listesi', label: 'Ödevler',
      build: async () => studentHomeworkDoc(student,
        await app.api.all(`/v1/students/${id}/homework`, {}, { limit: 100, max: 1000 })) }));
  }
  if (app.can('students_listCanteenTransactions') && app.enabled('canteen')) {
    items.push(documentButtons(app, { title: 'Kantin hesap dökümü', label: 'Kantin dökümü',
      build: async () => {
        const from = addDays(today, -30);
        const [summary, rows] = await Promise.all([app.api.get(`/v1/students/${id}/canteen`),
          app.api.all(`/v1/students/${id}/canteen/transactions`, { from, to: today }, { limit: 100, max: 3000 })]);
        return canteenDoc(student, summary, rows, { from, to: today });
      } }));
  }
  if (app.can('admin_getFinanceStatement') && app.enabled('finance')) {
    items.push(documentButtons(app, { title: 'Kurum cari hesap ekstresi', label: 'Hesap ekstresi',
      build: async () => {
        const from = `${year}-09-01`;
        return statementDoc(student, await app.api.get(`/v1/admin/finance/accounts/${id}/statement`,
          { from, to: today }));
      } }));
  }
  const row = toolbar(...items);
  return card('Belgeler', row, 'Kurum logolu A4 belge: önizleyin, yazdırın ya da PDF kaydedin');
}

function guardianCard(ctx, data) {
  const { app, id } = ctx;
  let rows = [...(data.guardians || [])].sort((a, b) => Number(a.slot) - Number(b.slot));
  const guardians = dataTable({
    columns: [
      { key: 'name', label: 'Veli', width: 'minmax(0, 1.4fr)', cell: (row) => stack(row.displayName, guardianRole(row)) },
      { key: 'phone', label: 'Telefon', cell: (row) => row.phoneMasked || '—' },
      { key: 'app', label: 'Uygulama', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(row.hasAppAccount ? badge('Hesabı var', 'good') : badge('Hesap yok', 'dim'));
        if (row.lastSeenAt) cell.append(h('small', '', `Son: ${fmtDateTime(row.lastSeenAt)}`));
        return cell;
      } },
      { key: 'canteen', label: 'Kantin yetkisi', cell: (row) => (row.canManageCanteen
        ? badge('Yönetebilir', 'info') : badge('Yok', 'dim')) },
      { key: 'actions', label: '', width: '130px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_patchGuardianLink',
          row.canManageCanteen ? 'Yetkiyi kaldır' : 'Yetki ver', { variant: 'ghost', onClick: () => toggle(row) }));
        return actions;
      } },
    ],
    rows,
    empty: empty('Kayıtlı veli yok', ctx.recordable ? 'Veliyi “Kayıt ve veliler” sekmesinden ekleyin.'
      : 'Veli bilgisi KM Öğrenci Yönetimi ekranından girilir.',
    ctx.recordable ? [button('Veli ekle', { onClick: () => ctx.select('record') })] : []),
  });

  async function toggle(row) {
    const next = !row.canManageCanteen;
    const ok = await confirmAction(app, {
      title: next ? `${row.displayName}: kantin yetkisi ver` : `${row.displayName}: kantin yetkisini kaldır`,
      description: next
        ? 'Veli uygulamada bu öğrencinin kantin kontrollerini (harcama limiti, engel, ürün kısıtı) yönetebilir.'
        : 'Veli uygulamada bu öğrencinin kantin kontrollerini artık değiştiremez.',
      confirmLabel: next ? 'Yetki ver' : 'Yetkiyi kaldır',
    });
    if (!ok) return;
    const saved = await runAction(app, () => app.api.patch(`/v1/admin/students/${id}/guardians/${row.id}`,
      { canManageCanteen: next }), { success: next ? 'Kantin yetkisi verildi.' : 'Kantin yetkisi kaldırıldı.' });
    if (saved) {
      rows = rows.map((item) => (item.id === saved.id ? saved : item));
      guardians.update({ rows });
    }
  }

  const box = card('Veliler', guardians.node);
  box.append(h('div', 'kit-card-body ak-muted', 'Kantin yetkisi yalnız velinin uygulamada kantin kontrollerini '
    + '(limit, engel, ürün kısıtı) yönetip yönetemeyeceğini belirler; kantin tahsilatıyla ve kurum carisiyle '
    + 'ilgisi yoktur.'));
  return box;
}

// ------------------------------------------------------------------ özet

export function overviewTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');

  const overview = loader(() => app.api.get(`/v1/students/${id}/overview`), renderOverview, { rows: 3, columns: 5 });
  const today = loader(() => app.api.get(`/v1/students/${id}/today`), renderToday, { rows: 4, columns: 2 });
  let goalsData = null;
  const goals = loader(() => app.api.get(`/v1/students/${id}/goals`), (data) => {
    goalsData = data;
    return renderGoals(data);
  }, { rows: 4, columns: 2 });
  // Tek hedef akışı (Akademi sözleşme README §14 madde 80): koçluk açık kurumda öneri → kabul (F-K18;
  // öğrenci kabul edince hedeflerine yazılır). Koçluk kapalı kurumda eski "kurum önerisi" (kullanımdan kalktı).
  const suggest = app.can('admin_createGoalProposal')
    ? actionButton(app, 'admin_createGoalProposal', 'Hedef öner', { variant: 'ghost',
      onClick: async () => {
        if (await goalDialog(app, id, { examTarget: ctx.student?.student?.examTarget })) goals.reload();
      } })
    : actionButton(app, 'admin_putGoalSuggestion', 'Hedef öner', { variant: 'ghost',
      onClick: () => suggestGoals(ctx, goalsData, () => goals.reload()) });

  let period = 'week';
  const periodSelect = selectBox([{ value: 'day', label: 'Bugün' }, { value: 'week', label: 'Bu hafta' },
    { value: 'month', label: 'Bu ay' }], period, (value) => { period = value; stats.reload(); }, { label: 'Dönem' });
  const stats = loader(() => app.api.get(`/v1/students/${id}/study-stats`, { period, date: todayIst() }),
    renderStats, { rows: 4, columns: 4 });
  const streak = loader(() => app.api.get(`/v1/students/${id}/streak`), renderStreak, { rows: 2, columns: 3 });
  const timer = loader(() => app.api.get(`/v1/students/${id}/study-timer`), renderTimer, { rows: 2, columns: 2 });
  const badges = loader(() => app.api.get(`/v1/students/${id}/badges`), renderBadges, { rows: 3, columns: 2 });
  const reports = pagedList({
    app,
    path: `/v1/students/${id}/weekly-reports`,
    limit: 10,
    columns: [
      { key: 'week', label: 'Hafta', cell: (row) => `${fmtDate(row.weekStart)} – ${fmtDate(row.weekEnd)}` },
      { key: 'generated', label: 'Oluşturuldu', cell: (row) => fmtDateTime(row.generatedAt) },
      { key: 'read', label: 'Veli', cell: (row) => (row.readAt ? badge(`Okudu · ${dayOf(row.readAt)}`, 'good')
        : badge('Okumadı', 'dim')) },
    ],
    onRow: (row) => weeklyReport(ctx, row),
    empty: { title: 'Henüz haftalık rapor yok', text: 'Rapor üretildikçe burada listelenir.' },
  });
  ctx.dispose(() => reports.destroy());

  node.append(
    card('Bugün', overview.node),
    ...[classHistoryCard(ctx)].filter(Boolean),
    grid2(card('Şimdi', today.node), panelCard('Hedefler', goals.node, [suggest])),
    panelCard('Çalışma', stats.node, [periodSelect.node]),
    grid2(card('Seri', streak.node), card('Çalışan zamanlayıcı', timer.node)),
    card('Rozetler', badges.node),
    card('Haftalık raporlar', reports.node, 'Velinin uygulamasına giden rapor'),
  );
  for (const view of [overview, today, goals, stats, streak, timer, badges]) view.reload();
  reports.reload();
  return { node };
}

function scheduleColumns() {
  return [
    { key: 'time', label: 'Saat', width: '110px', cell: (row) => `${fmtTime(row.startsAt)}–${fmtTime(row.endsAt)}` },
    { key: 'subject', label: 'Ders', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.subject?.name
      || ITEM_KIND[row.kind], row.topic?.name || (row.kind !== 'lesson' ? ITEM_KIND[row.kind] : '')) },
    { key: 'teacher', label: 'Öğretmen / derslik', cell: (row) => stack(row.teacher?.displayName, row.room) },
    { key: 'status', label: 'Durum', cell: (row) => (row.status === 'scheduled' ? '—'
      : stack(ITEM_STATUS[row.status]?.[0] || row.status, row.changeNote)) },
    { key: 'attendance', label: 'Yoklama', cell: (row) => (row.attendanceStatus
      ? statusBadge(ATTENDANCE, row.attendanceStatus) : '—') },
  ];
}

function renderOverview(data) {
  const presence = data.presence || {};
  const study = data.study || {};
  const line = h('div', 'kit-card-body ak-inline');
  line.append(presence.inInstitution === true ? badge('Kurumda', 'good')
    : badge(presence.inInstitution === false ? 'Kurumda değil' : 'Konum bilinmiyor', 'dim'));
  if (presence.attendanceStatus) line.append(statusBadge(ATTENDANCE, presence.attendanceStatus));
  if ((presence.signals || []).length) {
    line.append(h('span', 'ak-muted', `Sinyal: ${presence.signals.map((key) => SIGNALS[key] || key).join(', ')}`));
  }
  if (presence.lastSeenAt) line.append(h('span', 'ak-muted', `Son görülme ${fmtDateTime(presence.lastSeenAt)}`));
  const kpis = kpiRow([
    { label: 'Bugün çalışma', value: minutesText(study.minutes) },
    { label: 'Bugün soru', value: num(study.questions) },
    { label: 'Bekleyen ödev', value: num(data.pendingHomeworkCount), tone: data.pendingHomeworkCount ? 'warn' : '' },
    { label: 'Son deneme neti', value: data.lastExam ? net(data.lastExam.totalNet) : '—',
      title: data.lastExam ? `${data.lastExam.name} · ${fmtDate(data.lastExam.date)}` : 'Henüz deneme yok.' },
    isSet(data.tabletSecondsToday) ? { label: 'Tablet (bugün)', value: secondsText(data.tabletSecondsToday) } : null,
  ].filter(Boolean));
  const lessons = table(scheduleColumns(), (data.todayItems || []).filter((item) => item.kind !== 'break'),
    'Bugün ders yok');
  return [line, kpis, lessons];
}

function renderToday(data) {
  const goal = (entry, format) => {
    if (!entry) return '—';
    return isSet(entry.target) ? `${format(entry.done)} / ${format(entry.target)}` : `${format(entry.done)} (hedef yok)`;
  };
  const nodes = [kv([
    ['Şu an', itemText(data.currentItem) || 'Ders yok'],
    ['Sıradaki', itemText(data.nextItem) || '—'],
    ['Günlük soru', goal(data.goals?.questions, (value) => `${num(value)} soru`)],
    ['Günlük süre', goal(data.goals?.minutes, minutesText)],
    ['Yaklaşan deneme', data.upcomingExam ? `${data.upcomingExam.name} · ${fmtDate(data.upcomingExam.date)}` : '—'],
    ['Tekrar zamanı gelen', `${num(data.reviewDue?.count || 0)} konu`],
    ['Son duyuru', data.latestAnnouncement ? `${data.latestAnnouncement.title} · `
      + `${data.latestAnnouncement.readAt ? 'okundu' : 'okunmadı'}` : '—'],
  ])];
  if ((data.homeworkDue || []).length) {
    nodes.push(table([
      { key: 'title', label: 'Teslimi yaklaşan ödev', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, row.subject?.name) },
      { key: 'due', label: 'Son gün', cell: (row) => fmtDateTime(row.dueAt) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(HOMEWORK_STATUS, row.status) },
    ], data.homeworkDue, 'Ödev yok'));
  }
  return nodes;
}

function netsText(list) {
  return (list || []).map((item) => `${EXAM_TYPES[item.examType] || item.examType} ${net(item.net)}`).join(' · ');
}

function renderGoals(data) {
  const nodes = [kv([
    ['Günlük soru', isSet(data.dailyQuestions) ? `${num(data.dailyQuestions)} soru` : '—'],
    ['Günlük süre', isSet(data.dailyMinutes) ? minutesText(data.dailyMinutes) : '—'],
    ['Haftalık deneme neti', net(data.weeklyExamNet)],
    ['Sınav hedef netleri', netsText(data.examTargetNets) || '—'],
    ['Son değişiklik', fmtDateTime(data.updatedAt)],
  ])];
  const suggestion = data.suggestion || {};
  const parts = [
    isSet(suggestion.dailyQuestions) ? `günde ${num(suggestion.dailyQuestions)} soru` : '',
    isSet(suggestion.dailyMinutes) ? `günde ${minutesText(suggestion.dailyMinutes)}` : '',
    isSet(suggestion.weeklyExamNet) ? `haftalık ${net(suggestion.weeklyExamNet)} net` : '',
    netsText(suggestion.examTargetNets),
  ].filter(Boolean);
  if (parts.length || suggestion.note) {
    const box = h('div', 'kit-card-body ak-stu-suggestion');
    box.append(h('b', '', `Kurum önerisi${suggestion.by ? ` — ${personName(suggestion.by)}` : ''}`
      + `${suggestion.at ? ` · ${fmtDateTime(suggestion.at)}` : ''}`), h('span', '', parts.join(' · ') || '—'));
    if (suggestion.note) box.append(h('small', 'ak-muted', suggestion.note));
    nodes.push(box);
  } else {
    nodes.push(h('div', 'kit-card-body ak-muted', 'Henüz kurum önerisi yok.'));
  }
  return nodes;
}

/** Sınav türü + net satırları (hedef önerisi penceresinde). */
function netRowsEditor(initial, set) {
  const rows = (initial || []).map((item) => ({ examType: item.examType, net: item.net }));
  const wrap = h('div', 'ak-stack');
  const list = h('div', 'ak-stack');
  const sync = () => set(rows.filter((row) => row.examType && Number.isFinite(row.net) && row.net >= 0)
    .map((row) => ({ examType: row.examType, net: row.net })));
  const paint = () => {
    list.replaceChildren();
    rows.forEach((row, index) => {
      const line = h('div', 'ak-inline');
      const type = selectBox(optionsOf(EXAM_TYPES), row.examType, (value) => { row.examType = value; sync(); },
        { label: 'Sınav türü' });
      const input = h('input', 'kit-input ak-stu-net');
      input.inputMode = 'decimal';
      input.placeholder = 'Net';
      input.value = isSet(row.net) ? String(row.net).replace('.', ',') : '';
      input.addEventListener('input', () => {
        const raw = input.value.trim().replace(',', '.');
        row.net = raw === '' ? null : Number(raw);
        sync();
      });
      line.append(type.node, input, button('Kaldır', { variant: 'ghost', onClick: () => { rows.splice(index, 1); paint(); } }));
      list.append(line);
    });
    if (!rows.length) list.append(h('span', 'ak-muted', 'Sınav hedef neti önerilmiyor.'));
    sync();
  };
  wrap.append(list, button('Sınav ekle', { variant: 'ghost', onClick: () => {
    rows.push({ examType: 'tyt', net: null });
    paint();
  } }));
  paint();
  return wrap;
}

async function suggestGoals(ctx, goals, done) {
  const { app, id } = ctx;
  const base = goals?.suggestion || {};
  const intOrNull = (value) => (isSet(value) && Number.isFinite(Number(value)) ? Math.round(Number(value)) : null);
  const numOrNull = (value) => (isSet(value) && Number.isFinite(Number(value)) ? Number(value) : null);
  const result = await formDialog(app, {
    title: 'Hedef öner',
    description: 'Öneri öğrencinin hedeflerinin yanında “kurum önerisi” olarak görünür; öğrencinin kendi '
      + 'hedefini değiştirmez.',
    value: { dailyQuestions: base.dailyQuestions ?? null, dailyMinutes: base.dailyMinutes ?? null,
      weeklyExamNet: base.weeklyExamNet ?? null, note: base.note || '' },
    fields: [
      { key: 'dailyQuestions', label: 'Günlük soru', type: 'number', min: 0, max: 5000 },
      { key: 'dailyMinutes', label: 'Günlük çalışma (dakika)', type: 'number', min: 0, max: 1440 },
      { key: 'weeklyExamNet', label: 'Haftalık deneme neti', type: 'number', min: 0 },
      { key: 'examTargetNets', label: 'Sınav hedef netleri', type: 'custom',
        render: (draft, set) => netRowsEditor(base.examTargetNets || goals?.examTargetNets, set) },
      { key: 'note', label: 'Not', type: 'textarea', maxLength: 300, wide: true },
    ],
    submitLabel: 'Öneriyi kaydet',
    onSubmit: (draft, { key }) => app.api.put(`/v1/admin/students/${id}/goal-suggestion`, {
      dailyQuestions: intOrNull(draft.dailyQuestions),
      dailyMinutes: intOrNull(draft.dailyMinutes),
      weeklyExamNet: numOrNull(draft.weeklyExamNet),
      examTargetNets: draft.examTargetNets || [],
      note: String(draft.note || '').trim() || null,
    }, { key }),
  });
  if (result) {
    app.toast('Hedef önerisi kaydedildi.', 'good');
    done?.();
  }
}

function renderStats(data) {
  const totals = data.totals || {};
  const previous = data.previous || {};
  const nodes = [kpiRow([
    { label: 'Süre', value: minutesText(totals.minutes),
      delta: change(totals.minutes, previous.minutes, `Önceki dönem ${minutesText(previous.minutes)}`) },
    { label: 'Soru', value: num(totals.questions),
      delta: change(totals.questions, previous.questions, `Önceki dönem ${num(previous.questions)}`) },
    { label: 'Doğru / yanlış / boş', value: `${num(totals.correct)} / ${num(totals.wrong)} / ${num(totals.blank)}` },
    { label: 'Doğruluk', value: percent(totals.accuracyPercent) },
    { label: 'Hedefi tutan gün', value: num(data.goals?.daysGoalMet || 0) },
  ]), h('div', 'kit-card-body ak-muted', `${fmtDate(data.from)} – ${fmtDate(data.to)}`
    + `${isSet(data.goals?.dailyQuestions) ? ` · günlük hedef ${num(data.goals.dailyQuestions)} soru` : ''}`
    + `${isSet(data.goals?.dailyMinutes) ? ` · ${minutesText(data.goals.dailyMinutes)}` : ''}`)];
  const series = data.series || [];
  if (series.length > 1) {
    nodes.push(lineChart(series.map((point) => ({ label: shortDate(point.date), value: point.minutes || 0 })),
      { valueFormat: (value) => `${Math.round(value)} dk` }));
  }
  nodes.push(table([
    { key: 'subject', label: 'Ders', width: 'minmax(0, 2fr)', cell: (row) => row.subject?.name || '—' },
    { key: 'minutes', label: 'Süre', align: 'num', cell: (row) => minutesText(row.minutes) },
    { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questions) },
    { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => percent(row.accuracyPercent, 0) },
  ], data.bySubject, 'Bu dönemde çalışma kaydı yok'));
  return nodes;
}

function renderStreak(data) {
  const days = (data.calendar || []).slice(-42);
  const strip = h('div', 'ak-stu-streak');
  for (const day of days) {
    const cell = h('span', `ak-stu-streak-day${day.met ? ' ak-stu-met' : ''}`);
    cell.title = `${fmtDate(day.date)} · ${day.met ? 'hedef tuttu' : 'tutmadı'}`;
    strip.append(cell);
  }
  const met = days.filter((day) => day.met).length;
  return [kpiRow([
    { label: 'Güncel seri', value: `${num(data.current)} gün` },
    { label: 'En uzun seri', value: `${num(data.best)} gün` },
    { label: 'Bugün', value: data.todayMet ? 'Hedef tuttu' : 'Henüz değil' },
  ]), padded(strip, h('small', 'ak-muted', days.length
    ? `Son ${num(days.length)} günde ${num(met)} gün hedef tuttu · son: ${fmtDate(data.lastMetDate)}`
    : 'Takvim boş.'))];
}

function renderTimer(data) {
  const timer = data?.timer;
  if (!timer) return h('div', 'kit-card-body ak-muted', 'Şu an çalışan zamanlayıcı yok.');
  return kv([
    ['Mod', timer.mode === 'pomodoro' ? `Pomodoro (${num(timer.pomodoro?.workMinutes || 25)}+`
      + `${num(timer.pomodoro?.breakMinutes || 5)} dk)` : 'Serbest'],
    ['Ders / konu', [timer.subject?.name, timer.topic?.name].filter(Boolean).join(' · ') || '—'],
    ['Başladı', fmtDateTime(timer.startedAt)],
    ['Planlanan', isSet(timer.plannedMinutes) ? minutesText(timer.plannedMinutes) : '—'],
    ['Cihaz', timer.deviceId],
  ]);
}

function renderBadges(data) {
  const items = [...(data.items || [])].sort((a, b) => Number(Boolean(b.earnedAt)) - Number(Boolean(a.earnedAt)));
  return table([
    { key: 'title', label: 'Rozet', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, row.context || row.description) },
    { key: 'state', label: 'Durum', cell: (row) => {
      if (row.earnedAt) return badge(`Kazandı · ${dayOf(row.earnedAt)}`, 'good');
      return row.progress?.target ? `${num(row.progress.current)} / ${num(row.progress.target)}` : '—';
    } },
  ], items, 'Rozet yok');
}

function weeklyReport(ctx, row) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: `Haftalık rapor · ${fmtDate(row.weekStart)} – ${fmtDate(row.weekEnd)}`,
    subtitle: row.readAt ? `Veli okudu: ${fmtDateTime(row.readAt)}` : 'Veli henüz okumadı' });
  const view = loader(() => app.api.get(`/v1/students/${id}/weekly-reports/${row.weekStart}`), (report) => {
    const study = report.study || {};
    const homework = report.homework || {};
    const attendance = report.attendance || {};
    const nodes = [kpiRow([
      { label: 'Çalışma süresi', value: minutesText(study.minutes),
        delta: change(study.minutes, study.previousMinutes, `Önceki hafta ${minutesText(study.previousMinutes)}`) },
      { label: 'Çözülen soru', value: num(study.questions),
        delta: change(study.questions, study.previousQuestions, `Önceki hafta ${num(study.previousQuestions)}`) },
      { label: 'Doğruluk', value: percent(study.accuracyPercent) },
      // Akademi README §14 madde 85: öğretmen kontrolü sayılır (eksik 0,5, yapmadı 0).
      { label: 'Ödev', value: `${num(homework.completed)} / ${num(homework.assigned)}`,
        title: `${homeworkTally(homework)} · tamamlama ${percent(homework.completionRatePercent)}` },
      isSet(report.tabletSeconds) ? { label: 'Tablet', value: secondsText(report.tabletSeconds) } : null,
    ].filter(Boolean))];
    nodes.push(card('Yoklama', kv([
      ['Geldi', num(attendance.present)], ['Gelmedi', num(attendance.absent)],
      ['Geç kaldı', num(attendance.late)], ['İzinli', num(attendance.excused)],
    ])));
    const topics = report.completedTopics || [];
    nodes.push(card(`Tamamlanan konular (${num(topics.length)})`, padded(topics.length
      ? h('span', '', topics.map((topic) => topic.name).join(' · ')) : h('span', 'ak-muted', 'Bu hafta tamamlanan konu yok.'))));
    nodes.push(card('Denemeler', table([
      { key: 'name', label: 'Deneme', width: 'minmax(0, 2fr)', cell: (exam) => stack(exam.name, EXAM_TYPES[exam.type]) },
      { key: 'date', label: 'Tarih', cell: (exam) => fmtDate(exam.date) },
      { key: 'net', label: 'Net', align: 'num', cell: (exam) => net(exam.totalNet) },
    ], report.exams, 'Bu hafta deneme yok')));
    if (isSet(report.canteenSpentMinor)) {
      const canteen = card('Kantin (kurum carisinden ayrı)', kv([['Haftalık kantin harcaması', money(report.canteenSpentMinor)]]));
      canteen.classList.add('ak-money-canteen');
      nodes.push(canteen);
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

// --------------------------------------------------------------- program

export function scheduleTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let week = weekStart(todayIst());
  const label = h('b', 'ak-stu-period');
  const paintLabel = () => { label.textContent = `${fmtDate(week)} – ${fmtDate(addDays(week, 6))}`; };
  const move = (days) => { week = days ? addDays(week, days) : weekStart(todayIst()); paintLabel(); view.reload(); };
  node.append(toolbar(
    button('‹ Önceki hafta', { variant: 'ghost', onClick: () => move(-7) }), label,
    button('Sonraki hafta ›', { variant: 'ghost', onClick: () => move(7) }),
    button('Bu hafta', { variant: 'ghost', onClick: () => move(0) }),
  ));
  const view = loader(() => app.api.get(`/v1/students/${id}/schedule`, { week }), renderWeek, { rows: 6, columns: 6 });
  node.append(view.node);
  paintLabel();
  view.reload();
  return { node };
}

function renderWeek(data) {
  const nodes = [];
  const changes = data.changes || [];
  if (changes.length) {
    nodes.push(alertBox(`Bu hafta ${num(changes.length)} değişiklik: ${changes.slice(0, 4)
      .map((item) => `${CHANGE_KIND[item.kind] || item.kind}${item.note ? ` (${item.note})` : ''}`).join(' · ')}`
      + `${changes.length > 4 ? ' …' : ''}`, 'warn'));
  }
  const lessons = (day) => (day.items || []).filter((item) => item.kind !== 'break');
  const days = (data.days || []).filter((day) => day.dayOfWeek !== 'sun' || lessons(day).length);
  const grid = h('div', 'ak-stu-week');
  grid.style.setProperty('--ak-stu-days', String(Math.max(1, days.length)));
  const today = todayIst();
  for (const day of days) {
    const column = h('div', `ak-stu-day${day.date === today ? ' ak-stu-today' : ''}`);
    column.append(h('div', 'ak-stu-day-head', `${DAY_LABEL[day.dayOfWeek] || day.dayOfWeek} · ${shortDate(day.date)}`));
    const items = lessons(day);
    if (!items.length) column.append(h('span', 'ak-muted', 'Ders yok'));
    for (const item of items) column.append(lessonBlock(item));
    grid.append(column);
  }
  nodes.push(grid);
  return nodes;
}

function lessonBlock(item) {
  const box = h('div', `ak-stu-lesson ak-stu-lesson-${item.status}`);
  box.append(h('span', 'ak-stu-lesson-time', `${fmtTime(item.startsAt)}–${fmtTime(item.endsAt)}`),
    h('b', '', item.subject?.name || ITEM_KIND[item.kind] || 'Ders'));
  const meta = [item.kind !== 'lesson' ? ITEM_KIND[item.kind] : '', item.teacher?.displayName, item.room]
    .filter(Boolean).join(' · ');
  if (meta) box.append(h('small', '', meta));
  if (item.topic?.name) box.append(h('small', '', item.topic.name));
  const tags = h('div', 'ak-inline');
  if (item.status !== 'scheduled') tags.append(statusBadge(ITEM_STATUS, item.status));
  if (item.attendanceStatus) tags.append(statusBadge(ATTENDANCE, item.attendanceStatus));
  if (tags.childElementCount) box.append(tags);
  if (item.changeNote) box.append(h('small', '', item.changeNote));
  return box;
}

// ---------------------------------------------------- müfredat & ilerleme

export function curriculumTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let historical = false;
  let focusId = null;
  const view = loader(() => app.api.get(`/v1/students/${id}/curriculum`,
    { includeTopics: true, includeHistorical: historical || undefined }), (data) => {
    setTimeout(applyFocus, 0);
    return renderCurriculum(ctx, data);
  }, { rows: 6, columns: 4 });

  let scope = 'due';
  const scopeSelect = selectBox([{ value: 'due', label: 'Zamanı gelenler' }, { value: 'upcoming', label: 'Yaklaşanlar' },
    { value: 'all', label: 'Tümü' }], scope, (value) => { scope = value; queue.reload(); }, { label: 'Kapsam' });
  const queue = pagedList({
    app,
    path: `/v1/students/${id}/review-queue`,
    query: () => ({ scope }),
    limit: 20,
    columns: [
      { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => stack(row.topic?.name,
        subjectLabel(ctx, row.topic?.subjectId)) },
      { key: 'due', label: 'Tekrar günü', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(h('span', '', fmtDate(row.dueDate)));
        if (row.overdueDays) cell.append(badge(`${num(row.overdueDays)} gün gecikti`, 'bad'));
        return cell;
      } },
      { key: 'stage', label: 'Aşama', cell: (row) => `${num(row.stage)}/4 · ${num(row.intervalDays)} gün` },
      { key: 'reason', label: 'Neden', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(h('span', '', REVIEW_REASON[row.reason] || row.reason));
        if (row.priority) cell.append(badge('Öncelikli', 'warn'));
        return cell;
      } },
      { key: 'res', label: '', width: '90px', cell: (row) => linkButton('Kaynaklar', () => topicResources(ctx, row.topic.id)) },
    ],
    empty: { title: 'Bu kapsamda tekrar konusu yok' },
  });
  ctx.dispose(() => queue.destroy());

  function applyFocus() {
    if (!focusId) return;
    const target = [...view.node.querySelectorAll('[data-program]')].find((item) => item.dataset.program === focusId);
    if (!target) return;
    focusId = null;
    target.scrollIntoView({ block: 'start', behavior: 'smooth' });
    target.classList.add('ak-stu-focus');
    const firstUnit = target.querySelector('details');
    if (firstUnit && !firstUnit.open) firstUnit.open = true;
  }

  // Müfredat SETİ (F-C7, README §14 madde 68): varsayılan / elle / sınıftan; ekle, çıkar, varsayılana
  // döndür. Değişince aşağıdaki ilerleme ağacı yeniden okunur (etkin müfredat değişti).
  const set = app.can('admin_getStudentCurricula')
    ? studentCurriculumSet(app, id, { onChanged: () => view.reload() }) : null;
  if (set) ctx.dispose(() => set.destroy());
  node.append(...[set ? panelCard('Müfredat seti', set.node) : null,
    toolbar(checkbox('Geçmiş programları da göster', (on) => { historical = on; view.reload(); })),
    view.node, panelCard('Tekrar kuyruğu', queue.node, [scopeSelect.node])].filter(Boolean));
  set?.reload();
  view.reload();
  queue.reload();
  return { node, focus({ programId } = {}) { focusId = programId || null; applyFocus(); } };
}

function renderCurriculum(ctx, data) {
  const nodes = [];
  for (const warning of data.warnings || []) nodes.push(alertBox(CURRICULUM_WARNINGS[warning] || warning, 'warn'));
  nodes.push(h('div', 'ak-muted', `Seviye ${gradeText(data.gradeLevel)} · alan ${TRACKS[data.track] || '—'} · `
    + `hedef ${EXAM_TARGETS[data.examTarget] || '—'} · çözümlendi ${fmtDateTime(data.resolvedAt)}`));
  const programs = data.programs || [];
  if (!programs.length) {
    nodes.push(empty('Program yok', 'Öğrencinin sınıfına seviye girilince (Öğrenciler → Sınıflar) müfredat kendiliğinden bağlanır.'));
  }
  for (const entry of programs) nodes.push(programCard(ctx, entry));
  return nodes;
}

function programCard(ctx, entry) {
  const program = entry.program || {};
  const units = h('div', 'kit-card-body ak-stack');
  const sorted = [...(entry.units || [])].sort((a, b) => a.order - b.order);
  for (const unit of sorted) units.append(unitBlock(ctx, unit));
  if (!sorted.length) units.append(h('span', 'ak-muted', 'Ünite yok.'));
  const box = card(program.title || 'Program', units, [program.subject?.name, PROGRAM_KIND[program.kind], program.exam,
    PROGRAM_SOURCE[entry.source]].filter(Boolean).join(' · '));
  box.dataset.program = program.id || '';
  const head = box.querySelector('.kit-card-head');
  if (head) {
    const tools = h('div', 'ak-stu-card-tools');
    if (entry.historical) tools.append(badge('Geçmiş', 'dim'));
    tools.append(bar(entry.completionPercent), h('b', '', percent(entry.completionPercent, 0)));
    head.append(tools);
  }
  return box;
}

function unitBlock(ctx, unit) {
  const details = h('details', 'ak-stu-unit');
  const summary = h('summary', 'ak-stu-unit-head');
  const topics = [...(unit.topics || [])].sort((a, b) => a.order - b.order);
  summary.append(...[h('span', 'ak-stu-unit-title', unit.title), unit.custom ? badge('Kuruma özel', 'info') : null,
    h('span', 'kit-spacer'), bar(unit.completionPercent),
    h('span', 'ak-muted', `${percent(unit.completionPercent, 0)} · ${num(topics.length)} konu`)].filter(Boolean));
  details.append(summary);
  // Konu tablosu ünite ilk açıldığında kurulur (büyük programda DOM hafif kalsın).
  let built = false;
  details.addEventListener('toggle', () => {
    if (!details.open || built) return;
    built = true;
    details.append(table(topicColumns(ctx), topics, 'Konu yok'));
  });
  return details;
}

function topicColumns(ctx) {
  return [
    { key: 'title', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => {
      const cell = stack(row.title, isSet(row.weight) ? `ortalama ${num(row.weight, 1)} soru` : '');
      if (row.custom) cell.append(badge('Kuruma özel', 'info'));
      if (row.retired) cell.append(badge('Müfredattan kalktı', 'dim'));
      return cell;
    } },
    { key: 'status', label: 'Durum', cell: (row) => statusBadge(TOPIC_STATUS, row.status) },
    { key: 'solved', label: 'Çözülen', align: 'num', title: 'Çalışma + kaynak + ödev + tekrar',
      cell: (row) => `${num(row.solvedCount)} (${num(row.correctCount)} D · ${num(row.wrongCount)} Y)` },
    { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => percent(row.accuracyPercent, 0) },
    { key: 'last', label: 'Son çalışma', cell: (row) => dayOf(row.lastStudiedAt) },
    { key: 'review', label: 'Sonraki tekrar', cell: (row) => fmtDate(row.nextReviewAt) },
    { key: 'heat', label: 'Isı', width: '60px', cell: (row) => heat(row.heatLevel) },
    { key: 'res', label: '', width: '90px', cell: (row) => linkButton('Kaynaklar', () => topicResources(ctx, row.topicId)) },
  ];
}

function sectionProgressColumns(ctx, { withTopics = false } = {}) {
  return [
    { key: 'title', label: 'Bölüm', width: 'minmax(0, 2fr)', cell: (row) => stack(row.section?.title,
      [SECTION_KIND[row.section?.kind], row.section?.pageFrom ? `s. ${row.section.pageFrom}`
        + `${row.section.pageTo && row.section.pageTo !== row.section.pageFrom ? `–${row.section.pageTo}` : ''}` : '']
        .filter(Boolean).join(' · ')) },
    { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.section?.questionCount) },
    { key: 'done', label: 'Durum', cell: (row) => (row.done ? badge(`Bitti · ${dayOf(row.completedAt)}`, 'good')
      : badge('Bitmedi', 'dim')) },
    { key: 'result', label: 'Sonuç', cell: (row) => (row.correct + row.wrong + row.blank ? dqb(row) : '—') },
    withTopics ? { key: 'topics', label: 'Konular', cell: (row) => {
      const cell = h('div', 'ak-stu-links');
      (row.section?.topicIds || []).slice(0, 4).forEach((topicId, index) => {
        cell.append(linkButton(`Konu ${index + 1}`, () => topicResources(ctx, topicId), topicId));
      });
      return cell.childElementCount ? cell : '—';
    } } : null,
  ].filter(Boolean);
}

export function topicResources(ctx, topicId) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: 'Konu kaynakları', subtitle: 'Bu konu hangi kitaplardan çalışılır' });
  const view = loader(() => app.api.get(`/v1/students/${id}/topics/${topicId}/resources`), (data) => {
    panel.setTitle(`Konu kaynakları — ${data.topic?.name || topicId}`);
    const shelf = data.onShelf || [];
    const library = data.library || [];
    const nodes = [h('h4', 'ak-stu-h', `Öğrencinin rafında (${num(shelf.length)})`)];
    if (!shelf.length) nodes.push(h('div', 'ak-muted', 'Rafındaki kaynaklarda bu konuya ait bölüm yok.'));
    for (const entry of shelf) {
      nodes.push(card(entry.resource?.title || 'Kaynak', table(sectionProgressColumns(ctx), entry.sections, 'Bölüm yok'),
        resourceMeta(entry.resource)));
    }
    nodes.push(h('h4', 'ak-stu-h', `Kurum kütüphanesinde, rafta değil (${num(library.length)})`));
    if (!library.length) nodes.push(h('div', 'ak-muted', 'Kütüphanede bu konuya ait başka kaynak yok.'));
    for (const entry of library) {
      nodes.push(card(entry.resource?.title || 'Kaynak', table([
        { key: 'title', label: 'Bölüm', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, SECTION_KIND[row.kind]) },
        { key: 'pages', label: 'Sayfa', cell: (row) => (row.pageFrom ? `${row.pageFrom}–${row.pageTo || row.pageFrom}` : '—') },
        { key: 'questions', label: 'Soru', align: 'num', cell: (row) => num(row.questionCount) },
      ], entry.sections, 'Bölüm yok'), resourceMeta(entry.resource)));
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

// -------------------------------------------------------------- kaynaklar

export function resourcesTab(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  const filters = { source: '', subject: '' };
  const sourceSelect = selectBox(optionsOf(SHELF_SOURCE, { empty: 'Tüm kaynaklar' }), '', (value) => {
    filters.source = value; list.reload();
  }, { label: 'Nereden' });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => {
    filters.subject = value; list.reload();
  }, { label: 'Ders' });
  app.lookups.options('subjects', { empty: 'Tüm dersler' }).then((options) => subjectSelect.options(options))
    .catch(() => {});
  const list = pagedList({
    app,
    path: `/v1/students/${id}/resources`,
    query: () => ({ source: filters.source || undefined, subject: filters.subject || undefined }),
    columns: [
      { key: 'title', label: 'Kaynak', width: 'minmax(0, 2.2fr)', cell: (row) => stack(row.resource?.title, resourceMeta(row.resource)) },
      { key: 'subject', label: 'Ders', cell: (row) => (row.resource?.subjects || [])
        .map((subject) => subject.shortName || subject.name).join(', ') || '—' },
      { key: 'source', label: 'Nereden', cell: (row) => stack(SHELF_SOURCE[row.source] || row.source,
        row.assignment?.packageName || (row.assignment?.targetDate ? `hedef ${fmtDate(row.assignment.targetDate)}` : '')) },
      { key: 'progress', label: 'İlerleme', width: '190px', cell: (row) => {
        const progress = row.progress || {};
        const cell = h('div', 'ak-inline');
        cell.append(bar(progress.percent), h('span', '', `${percent(progress.percent, 0)} · `
          + `${num(progress.sectionsDone)}/${num(progress.sectionsTotal)}`));
        if (progress.behind) cell.append(badge('Geride', 'warn'));
        return cell;
      } },
      { key: 'accuracy', label: 'Doğruluk', align: 'num', cell: (row) => percent(row.progress?.accuracyPercent, 0) },
      { key: 'last', label: 'Son çalışma', cell: (row) => dayOf(row.progress?.lastStudiedAt) },
    ],
    onRow: (row) => shelfItem(ctx, row.resource.id),
    empty: { title: 'Rafta kaynak yok', text: 'Kaynak atamak için Kaynaklar ekranını kullanın.' },
  });
  ctx.dispose(() => list.destroy());
  node.append(toolbar(sourceSelect.node, subjectSelect.node, h('span', 'kit-spacer'),
    button('Kaynak ata', { variant: 'ghost', onClick: () => app.go('resources') })), list.node);
  list.reload();
  return { node };
}

function shelfItem(ctx, resourceId) {
  const { app, id } = ctx;
  const panel = subDrawer(ctx, { title: 'Kaynak', subtitle: 'İçindekiler ve bölüm ilerlemesi' });
  const view = loader(() => app.api.get(`/v1/students/${id}/resources/${resourceId}`), (item) => {
    const resource = item.resource || {};
    const progress = item.progress || {};
    panel.setTitle(resource.title || 'Kaynak');
    const nodes = [];
    if (progress.behind) nodes.push(alertBox('Öğrenci bu kaynakta hedef tarihin gerisinde.', 'warn'));
    nodes.push(kpiRow([
      { label: 'İlerleme', value: percent(progress.percent, 0) },
      { label: 'Bölüm', value: `${num(progress.sectionsDone)} / ${num(progress.sectionsTotal)}` },
      { label: 'Soru', value: `${num(progress.questionsSolved)} / ${num(progress.questionsTotal)}` },
      { label: 'Doğruluk', value: percent(progress.accuracyPercent, 0) },
    ]), card('', kv([
      ['Tür', RESOURCE_KIND[resource.kind] || resource.kind],
      ['Yayınevi / ISBN', [resource.publisher, resource.isbn].filter(Boolean).join(' · ') || '—'],
      ['Ders', (resource.subjects || []).map((subject) => subject.name).join(', ') || '—'],
      ['Nereden', `${SHELF_SOURCE[item.source] || item.source}${item.assignment?.packageName
        ? ` · ${item.assignment.packageName}` : ''}`],
      ['Atandı', item.assignment?.assignedAt ? `${fmtDateTime(item.assignment.assignedAt)}${item.assignment.targetDate
        ? ` · hedef ${fmtDate(item.assignment.targetDate)}` : ''}` : '—'],
      ['Rafa eklendi', fmtDateTime(item.addedAt)],
      ['Son çalışma', fmtDateTime(progress.lastStudiedAt)],
    ])));
    const units = [...(item.units || [])].sort((a, b) => a.order - b.order);
    for (const unit of units) {
      const sections = [...(unit.sections || [])].sort((a, b) => (a.section?.order || 0) - (b.section?.order || 0));
      nodes.push(card(unit.title, table(sectionProgressColumns(ctx, { withTopics: true }), sections, 'Bölüm yok')));
    }
    if (!units.length) nodes.push(empty('İçindekiler yok', 'Kaynağın bölümleri Kaynaklar ekranında tanımlanır.'));
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

