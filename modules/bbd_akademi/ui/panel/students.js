// BBD Akademi — Öğrenciler (F-A2): öğrenci, sınıf, çalışma grubu ve veli.
//
// VERİ KM'DE (A5). Öğrenci, sınıf ve veli KM'nin Öğrenciler modülünden
// (`bbd_students`) Akademi'ye eşitlenir; seviye, alan ve hedef sınav burada
// SALT-OKUNURDUR ve düğme o ekranı açar. Akademi'de düzenlenen yalnız
// Akademi'ye ait eşlemelerdir: sınıfın dönemi ve rehber öğretmeni, çalışma
// grupları, velinin kantin yetkisi, hedef önerisi.
//
// ÖĞRENCİ ÇEKMECESİ. `openStudent` çekmecenin kabuğudur: başlık
// (`admin_getStudent`) + iç sekmeler (students-detail.js, students-records.js).
// Her sekme ilk açıldığında kurulur ve çekmece kapanana kadar açık kalır.
// Başka bölümler `app.go('students', {studentId, tab?})` ile doğrudan açar;
// `{classGroupId}` Sınıflar sekmesinde o sınıfı, `{view}` bir sekmeyi açar.
//
// HIZ: listeler imleçlidir (`pagedList`), arama 250 ms bekler, sınıf/grup
// seçenekleri önbellekli `app.lookups`'tan gelir; satır başına istek atılmaz.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge, tabBar } from '../../ui-kit/layout.js';
import {
  EXAM_TARGETS, GRADE_LEVELS, TRACKS, fmtDateTime, gradeText, loader, num, openDrawer, optionsOf,
  pagedList, searchBox, sectionHead, selectBox, toolbar,
} from './common.js';
import {
  STUDENT_STATUS, curriculumTab, linkButton, openKmStudents, overviewTab, renderHeader, resourcesTab,
  scheduleTab, stack,
} from './students-detail.js';
import {
  attendanceTab, canteenTab, examsTab, financeTab, homeworkTab, requestsTab, studyTab,
} from './students-records.js';
import { classesView, groupsView } from './students-groups.js';
import { learningPanel } from './students-learning.js';
import { flashcardsPanel } from './students-flashcards.js';
import { goalsPanel } from './students-goals.js';
import { coachingPanel } from './students-coaching.js';

/** Gömülebilir panel (`fn(app, studentId)` → {node, shown, destroy}) çekmece sekmesi olur. */
const embedded = (fn) => (ctx) => fn(ctx.app, ctx.id);

export function section(app) {
  loadStyles(import.meta.url, 'students.css');
  const node = h('div', 'ak-stack ak-stu');
  node.append(sectionHead('Öğrenciler', {
    subtitle: 'KM öğrenci dizininin Akademi aynası: öğrenciler, sınıflar, çalışma grupları ve veliler.' }));

  const open = (studentId, options) => openStudent(app, studentId, options);
  const tabs = tabBar([
    { key: 'students', label: 'Öğrenciler' },
    { key: 'classes', label: 'Sınıflar' },
    { key: 'groups', label: 'Çalışma grupları' },
    { key: 'guardians', label: 'Veliler' },
  ], 'students', (key) => show(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  const bodies = {};
  const builders = {
    students: () => studentsView(app, { open }),
    classes: () => classesView(app, { openStudent: open }),
    groups: () => groupsView(app, { openStudent: open,
      onChange: () => bodies.students?.refreshOptions() }),
    guardians: () => guardiansView(app, { open }),
  };

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.classGroupId ? 'classes' : (builders[payload?.view] ? payload.view : null);
      if (!started) {
        started = true;
        if (!wanted) show('students');
      }
      if (wanted) {
        tabs.select(wanted, false);
        show(wanted);
      }
      if (payload?.classGroupId) bodies.classes.openClass(payload.classGroupId);
      if (payload?.studentId) open(payload.studentId, { tab: payload.tab, payload });
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// --------------------------------------------------------------- öğrenciler

function studentsView(app, { open }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', classGroupId: '', studentGroupId: '', gradeLevel: '', track: '', status: '' };
  const search = searchBox({ placeholder: 'Ad, soyad, okul no ya da STU-…', onSearch: (q) => {
    filters.q = q; reload();
  } });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm çalışma grupları' }], '', (value) => {
    filters.studentGroupId = value; reload();
  }, { label: 'Çalışma grubu' });
  const gradeSelect = selectBox([{ value: '', label: 'Tüm seviyeler' },
    ...GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }))], '', (value) => {
    filters.gradeLevel = value; reload();
  }, { label: 'Seviye' });
  const trackSelect = selectBox(optionsOf(TRACKS, { empty: 'Tüm alanlar' }), '', (value) => {
    filters.track = value; reload();
  }, { label: 'Alan' });
  const statusSelect = selectBox(optionsOf(STUDENT_STATUS, { empty: 'Tüm durumlar' }), '', (value) => {
    filters.status = value; reload();
  }, { label: 'Durum' });

  function refreshOptions() {
    app.lookups.options('classGroups', { empty: 'Tüm sınıflar' })
      .then((options) => classSelect.options(options)).catch(() => {});
    app.lookups.options('studentGroups', { empty: 'Tüm çalışma grupları' })
      .then((options) => groupSelect.options(options)).catch(() => {});
  }
  refreshOptions();

  function clearFilters() {
    Object.keys(filters).forEach((key) => { filters[key] = ''; });
    search.node.value = '';
    for (const select of [classSelect, groupSelect, gradeSelect, trackSelect, statusSelect]) select.set('');
    reload();
  }

  const hint = h('div', 'kit-hint ak-stu-hint');
  hint.append(h('span', '', 'Seviye, alan ve hedef sınav burada salt-okunurdur: KM\'nin Öğrenciler modülünde '
    + 'tanımlanır ve Akademi\'ye eşitlenir. Öğrenci eklemek ya da bilgisini düzeltmek için o ekranı kullanın.'),
  button('Öğrenciler modülünü aç', { onClick: () => openKmStudents(app, null) }));

  node.append(hint, toolbar(search.node, classSelect.node, groupSelect.node, gradeSelect.node, trackSelect.node,
    statusSelect.node, button('Süzgeçleri temizle', { variant: 'ghost', onClick: () => clearFilters() })));

  const list = pagedList({
    app,
    path: '/v1/admin/students',
    // `STU-…` yazılırsa öğrenci numarasıyla birebir aranır.
    query: () => {
      const exactRef = /^STU-[A-Za-z0-9]+$/i.test(filters.q);
      return {
        q: exactRef ? undefined : filters.q || undefined,
        externalRef: exactRef ? filters.q.toUpperCase() : undefined,
        classGroupId: filters.classGroupId || undefined,
        studentGroupId: filters.studentGroupId || undefined,
        gradeLevel: filters.gradeLevel || undefined,
        track: filters.track || undefined,
        status: filters.status || undefined,
      };
    },
    columns: [
      { key: 'name', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = stack(row.displayName, [row.externalRef, row.schoolNo ? `okul no ${row.schoolNo}` : '']
          .filter(Boolean).join(' · '));
        if (row.status === 'missing') cell.append(badge(STUDENT_STATUS.missing[0], 'warn'));
        return cell;
      } },
      { key: 'class', label: 'Sınıf', cell: (row) => row.classGroup?.name || '—' },
      { key: 'grade', label: 'Seviye', cell: (row) => gradeText(row.gradeLevel) },
      { key: 'track', label: 'Alan', cell: (row) => TRACKS[row.track] || '—' },
      { key: 'target', label: 'Hedef', cell: (row) => EXAM_TARGETS[row.examTarget] || '—' },
      { key: 'app', label: 'Uygulama', width: 'minmax(0, 1.3fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(row.appUsers?.student ? badge('Öğrenci', 'good') : badge('Öğrenci yok', 'dim'),
          h('span', 'ak-muted', `${num(row.appUsers?.guardians || 0)}/${num(row.guardianCount)} veli`));
        return cell;
      } },
      { key: 'seen', label: 'Son görülme', width: '130px', cell: (row) => fmtDateTime(row.lastSeenAt) },
    ],
    onRow: (row) => open(row.id),
    empty: { title: 'Bu süzgece uyan öğrenci yok', text: 'Aramayı değiştirin ya da süzgeçleri temizleyin.',
      actions: [button('Süzgeçleri temizle', { onClick: () => clearFilters() })] },
  });
  node.append(list.node);

  function reload() { return list.reload(); }
  return { node, reload, refreshOptions, destroy() { search.destroy(); list.destroy(); } };
}

// ------------------------------------------------------------------- veliler

function guardiansView(app, { open }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', hasAppAccount: '' };
  const search = searchBox({ placeholder: 'Veli ara', onSearch: (q) => { filters.q = q; list.reload(); } });
  const appSelect = selectBox([{ value: '', label: 'Tüm veliler' }, { value: 'yes', label: 'Uygulaması olan' },
    { value: 'no', label: 'Uygulaması olmayan' }], '', (value) => { filters.hasAppAccount = value; list.reload(); },
  { label: 'Uygulama' });
  node.append(toolbar(search.node, appSelect.node, h('span', 'kit-spacer'),
    h('span', 'ak-muted', 'Veli bilgisi KM\'den eşitlenir; öğrencinin adına tıklayınca detayı açılır.')));

  const list = pagedList({
    app,
    path: '/v1/admin/guardians',
    query: () => ({ q: filters.q || undefined,
      hasAppAccount: filters.hasAppAccount ? filters.hasAppAccount === 'yes' : undefined }),
    columns: [
      { key: 'name', label: 'Veli', width: 'minmax(0, 1.4fr)', cell: (row) => row.displayName || '—' },
      { key: 'phone', label: 'Telefon', cell: (row) => row.phoneMasked || '—' },
      { key: 'students', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-stu-links');
        for (const student of row.students || []) {
          cell.append(linkButton(`${student.displayName}${student.classGroup?.name
            ? ` (${student.classGroup.name})` : ''}`, () => open(student.id), 'Öğrenci detayını aç'));
        }
        return cell.childElementCount ? cell : '—';
      } },
      { key: 'app', label: 'Uygulama', cell: (row) => (row.hasAppAccount ? badge('Hesabı var', 'good')
        : badge('Hesap yok', 'dim')) },
      { key: 'seen', label: 'Son görülme', width: '130px', cell: (row) => fmtDateTime(row.lastSeenAt) },
    ],
    empty: { title: 'Bu süzgece uyan veli yok' },
  });
  node.append(list.node);
  return { node, reload: () => list.reload(), destroy() { search.destroy(); list.destroy(); } };
}

// ---------------------------------------------------------- öğrenci çekmecesi

/**
 * Öğrenci çekmecesi. `tab`: overview · schedule · curriculum · resources ·
 * homework · exams · attendance · study · learning · goals · coaching ·
 * flashcards · requests · finance · canteen. Öğrenme, hedef, koçluk ve kart
 * sekmeleri gömülü panellerdir (students-learning/-goals/-coaching/-flashcards.js);
 * `payload` (ör. `{learningTab}`, `{coachingTab}`) sekmenin `shown`'ına geçer.
 */
export function openStudent(app, studentId, { tab = 'overview', payload = null } = {}) {
  const disposers = [];
  let detail = null;
  const km = button('KM Öğrenciler\'de aç', { variant: 'ghost',
    onClick: () => openKmStudents(app, detail?.student || { id: studentId }) });
  const panel = openDrawer(app, { title: 'Öğrenci', subtitle: 'Yükleniyor…', actions: [km],
    onClose: () => {
      for (const fn of disposers.splice(0)) {
        try { fn(); } catch { /* temizlik yarıda kalmaz */ }
      }
    } });
  panel.node.classList.add('ak-stu-drawer');
  const subtitle = panel.node.querySelector('.kit-drawer-sub');

  // Başlık yüklenince çözülür (ör. Denemeler varsayılan türü hedef sınavdan seçer).
  let markReady;
  const ready = new Promise((resolve) => { markReady = resolve; });
  const ctx = {
    app,
    id: studentId,
    ready,
    get student() { return detail; },
    subjectNames: new Map(),
    dispose: (fn) => disposers.push(fn),
    select: (key, payload) => select(key, payload),
  };
  // Ders adları (önbellekli) — konu ve deneme satırlarında kimlik yerine ad yazılsın.
  app.lookups.subjects().then((list) => {
    for (const subject of list) ctx.subjectNames.set(subject.id, subject.name);
  }).catch(() => {});

  const head = loader(async () => {
    try {
      return await app.api.get(`/v1/admin/students/${studentId}`);
    } catch (failure) {
      markReady(null);
      throw failure;
    }
  }, (data) => {
    detail = data;
    markReady(data);
    const student = data.student || {};
    panel.setTitle(student.displayName || 'Öğrenci');
    if (subtitle) {
      subtitle.textContent = [student.externalRef, student.classGroup?.name,
        data.schoolNo ? `okul no ${data.schoolNo}` : ''].filter(Boolean).join(' · ');
    }
    return renderHeader(ctx, data);
  }, { rows: 4, columns: 4 });

  const specs = [
    { key: 'overview', label: 'Özet', build: overviewTab },
    { key: 'schedule', label: 'Program', build: scheduleTab },
    { key: 'curriculum', label: 'Müfredat & ilerleme', build: curriculumTab },
    { key: 'resources', label: 'Kaynaklar', build: resourcesTab },
    { key: 'homework', label: 'Ödevler', build: homeworkTab },
    { key: 'exams', label: 'Denemeler', build: examsTab },
    { key: 'attendance', label: 'Yoklama', build: attendanceTab },
    { key: 'study', label: 'Çalışma', build: studyTab },
    { key: 'learning', label: 'Öğrenme', build: embedded(learningPanel) },
    { key: 'goals', label: 'Hedef & özet', build: embedded(goalsPanel) },
    { key: 'coaching', label: 'Koçluk', build: embedded(coachingPanel) },
    { key: 'flashcards', label: 'Bilgi kartları', build: embedded(flashcardsPanel) },
    { key: 'requests', label: 'Talepler', build: requestsTab },
    app.has('finance') ? { key: 'finance', label: 'Kurum cari', build: financeTab } : null,
    { key: 'canteen', label: 'Kantin', build: canteenTab },
  ].filter(Boolean);
  const keys = specs.map((spec) => spec.key);
  const first = keys.includes(tab) ? tab : 'overview';
  const tabsBar = tabBar(specs.map(({ key, label }) => ({ key, label })), first, (key) => show(key));
  const slot = h('div', 'ak-stu-tabbody');
  const bodies = {};

  // Sekme gövdesi `{node, focus?, shown?, destroy?}`: `shown` her gösterimde,
  // `destroy` çekmece kapanınca çağrılır (gömülebilir panel sözleşmesi).
  function show(key, payload) {
    let body = bodies[key];
    if (!body) {
      body = specs.find((spec) => spec.key === key).build(ctx);
      bodies[key] = body;
      slot.append(body.node);
      if (body.destroy) disposers.push(() => body.destroy());
    }
    for (const [name, item] of Object.entries(bodies)) item.node.hidden = name !== key;
    body.shown?.(payload);
    if (payload) body.focus?.(payload);
  }

  function select(key, payload) {
    if (!keys.includes(key)) return;
    tabsBar.select(key, false);
    show(key, payload);
  }

  panel.body.append(head.node, tabsBar.node, slot);
  head.reload();
  show(first, first === tab ? payload : null);
  return { close: panel.close, select };
}
