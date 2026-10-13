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
// PASİF ÖĞRENCİ (km-merkezi-senkron §5.2): KM'de pasifleştirilen öğrenci
// Akademi listesinde varsayılan olarak görünmez; "Durum → Pasif" süzgeci
// (`status=inactive`) ile görünür ve çekmecesi SALT OKUNURDUR — yazma düğmeleri
// gerekçeli kapanır (`readOnlyApp`). İstisna kurum cari (`finance.*`): borç
// pasifken de tahsil edilebilmeli. Asıl kapı Akademi'dedir.
//
// HIZ: listeler imleçlidir (`pagedList`), arama 250 ms bekler, sınıf/grup
// seçenekleri önbellekli `app.lookups`'tan gelir; satır başına istek atılmaz.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge, tabBar } from '../../ui-kit/layout.js';
import {
  EXAM_TARGETS, GRADE_LEVELS, TRACKS, empty, fmtDateTime, gradeText, loader, num, openDrawer, optionsOf,
  pagedList, problem, searchBox, sectionHead, selectBox, toolbar,
} from './common.js';
import {
  STUDENT_STATUS, curriculumTab, linkButton, openKmStudents, overviewTab, renderHeader, resourcesTab,
  scheduleTab, stack,
} from './students-detail.js';
import {
  attendanceTab, canteenTab, examsTab, financeTab, homeworkTab, requestsTab, studyTab,
} from './students-records.js';
import { classesView, groupsView } from './students-groups.js';
import { locatePart, studentCard } from './sections.js';
import { learningPanel } from './students-learning.js';
import { flashcardsPanel } from './students-flashcards.js';
import { goalsPanel } from './students-goals.js';
import { coachingPanel } from './students-coaching.js';

/** Gömülebilir panel (`fn(app, studentId)` → {node, shown, destroy}) çekmece sekmesi olur. */
// (`ctx.app` pasif öğrencide salt okunur görünümdür — `readOnlyApp`.)
const embedded = (fn) => (ctx) => fn(ctx.app, ctx.id);

export function section(app) {
  loadStyles(import.meta.url, 'students.css');
  const node = h('div', 'ak-stack ak-stu');
  // ÖĞRENCİ YÖNETİMİ BURADA (13.10.2026): KM Öğrenci Yönetimi'nin listesi,
  // toplu işlemleri ve grupları `bbd_students.manager` yeteneğiyle çizilir;
  // satıra tıklamak öğrencinin TEK KARTINI açar (Akademi + KM kaydı). Yetenek
  // yoksa (modül kapalı ya da kullanıcının öğrenci yönetimi izni yok) eski
  // salt okunur Akademi listesi "Öğrenciler" olarak kalır.
  const manager = app.capability?.('bbd_students.manager') || null;
  node.append(sectionHead('Öğrenciler', { subtitle: manager
    ? 'Öğrenci ekleyin, sınıf ve gruplara atayın, velileri yönetin; öğrenciye tıklayınca tek kartı açılır.'
    : 'Öğrenciler, sınıflar, çalışma grupları ve veliler.' }));

  const open = (studentId, options) => openStudent(app, studentId, options);
  const openRef = (kmId, options) => openStudentByRef(app, kmId, options);
  // Yönetimde KM'nin üç sekmesi (liste, toplu işlemler, gruplar) Akademi'nin
  // şeridine katılır: tek şerit, tek "Öğrenciler".
  const kmOnly = Boolean(app.kmOnly?.());
  const specs = manager && kmOnly ? [
    // Akademi izni yok (0.5.3): yalnız KM kaydı — liste, toplu işlemler, gruplar.
    { key: 'km', label: 'Öğrenciler' },
    { key: 'kmBulk', label: 'Toplu işlemler' },
    { key: 'kmGroups', label: 'Gruplar' },
    { key: 'kmClasses', label: 'Sınıflar' },
  ] : manager ? [
    { key: 'km', label: 'Öğrenciler' },
    { key: 'kmBulk', label: 'Toplu işlemler' },
    { key: 'kmGroups', label: 'Gruplar' },
    // SINIF SEVİYEYİ TAŞIR (13.10.2026): seviye / alan / hedef sınav KM sınıf tanımında
    // ("Sınıflar"); Akademi sınıf eşlemesi (dönem, rehber öğretmen) ayrı sekmede.
    { key: 'kmClasses', label: 'Sınıflar' },
    { key: 'classes', label: 'Dönem ve rehber' },
    { key: 'guardians', label: 'Veliler ve uygulama' },
    { key: 'students', label: 'Uygulama kullanımı' },
  ] : [
    { key: 'students', label: 'Öğrenciler' },
    { key: 'classes', label: 'Sınıflar' },
    { key: 'groups', label: 'Çalışma grupları' },
    { key: 'guardians', label: 'Veliler' },
  ];
  const first = specs[0].key;
  const tabs = tabBar(specs, first, (key) => show(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  const bodies = {};
  let shared = null;                  // KM yönetimi: üç sekme tek örnek
  const kmMode = { km: 'list', kmBulk: 'bulk', kmGroups: 'groups', kmClasses: 'classes' };
  const managed = () => {
    shared = shared || managerView(app, manager, { openRef });
    return shared;
  };
  const builders = {
    km: managed,
    kmBulk: managed,
    kmGroups: managed,
    kmClasses: managed,
    students: () => studentsView(app, { open }),
    classes: () => classesView(app, { openStudent: open }),
    groups: () => groupsView(app, { openStudent: open }),
    guardians: () => guardiansView(app, { open }),
  };
  const known = new Set(specs.map((spec) => spec.key));

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      if (!bodies[key].node.isConnected) slot.append(bodies[key].node);
      bodies[key].reload();
    }
    const visible = bodies[key].node;
    for (const body of Object.values(bodies)) body.node.hidden = body.node !== visible;
    if (kmMode[key]) bodies[key].mode(kmMode[key]);
  }

  let started = false;
  return {
    node,
    shown(payload) {
      // `{view: 'groups'}` (eski Akademi grupları) yönetimde KM "Gruplar"ıdır.
      let wanted = payload?.classGroupId ? 'classes' : (known.has(payload?.view) ? payload.view : null);
      if (manager && ['groups', 'bulk', 'km', 'list', 'kmClasses'].includes(payload?.view)) {
        wanted = { groups: 'kmGroups', bulk: 'kmBulk', kmClasses: 'kmClasses' }[payload.view] || 'km';
      }
      if (!started) {
        started = true;
        if (!wanted) show(first);
      }
      if (wanted) {
        tabs.select(wanted, false);
        show(wanted);
      }
      if (payload?.classGroupId) bodies.classes.openClass(payload.classGroupId);
      if (payload?.studentId) open(payload.studentId, { tab: payload.tab, payload });
      else if (payload?.kmStudentId) openRef(payload.kmStudentId, { tab: payload.studentTab || 'record' });
    },
    destroy() {
      for (const body of new Set(Object.values(bodies))) body.destroy?.();
    },
  };
}

/** KM Öğrenci Yönetimi (liste + toplu işlemler + gruplar) — `bbd_students.manager`. */
function managerView(app, manager, { openRef }) {
  const node = h('div', 'ak-stu-km');
  let cleanup = null;
  return {
    node,
    reload() {
      if (cleanup) return;
      cleanup = manager(node, {
        capability: app.capability,
        hideModes: true,
        openStudent: (student) => openRef(student.kantinId, { tab: 'record', name: student.displayName,
          status: student.status }),
      });
    },
    mode(view) { cleanup?.setMode?.(view); },
    destroy() { cleanup?.(); cleanup = null; },
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
  // Boş süzgeç Akademi'de "etkin + bulunamayan" demektir; pasifler yalnız
  // `status=inactive` ile döner (sözleşme `admin_listStudents`).
  const statusSelect = selectBox(optionsOf(STUDENT_STATUS, { empty: 'Etkin ve bulunamayan' }), '', (value) => {
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

  // Bu liste Akademi'nin aynasıdır (uygulama hesapları, son görülme). Öğrenci
  // eklemek / düzeltmek yönetim listesindedir (KM kaydı).
  const managed = Boolean(app.capability?.('bbd_students.manager'));
  const hint = h('div', 'kit-hint ak-stu-hint');
  hint.append(h('span', '', managed
    ? 'Öğrencilerin BBD Akademi uygulamasını kullanma durumu. Öğrenci eklemek, sınıf ya da veli değiştirmek '
      + 'için “Öğrenciler” sekmesini kullanın.'
    : 'Seviye, alan ve hedef sınav burada salt-okunurdur: KM\'de yönetilir ve Akademi\'ye eşitlenir. Öğrenci '
      + 'eklemek ya da bilgisini düzeltmek için Öğrenci Yönetimi ekranını kullanın.'),
  button(managed ? 'Öğrenciler sekmesine git' : 'Öğrenci Yönetimi\'ni aç',
    { onClick: () => openKmStudents(app, null) }));

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
        if (row.status === 'inactive') cell.append(badge(STUDENT_STATUS.inactive[0], 'dim'));
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
    onRow: (row) => open(row.id, { status: row.status }),
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
 * Öğrenci çekmecesi (kartı). Beş sekme — Özet · Program ve yoklama · Ödev ve
 * çalışma · Ölçme ve gelişim · Kurum cari ve kantin (`sections.js` →
 * STUDENT_CARD) — ve her sekmede katlanır bölümler. `tab` sekme anahtarı ya da
 * bölüm (eski sekme) anahtarıdır: overview · goals · schedule · attendance ·
 * requests · homework · learning · study · flashcards · exams · curriculum ·
 * resources · coaching · finance · canteen. Öğrenme, hedef, koçluk ve kart
 * bölümleri gömülü panellerdir (students-learning/-goals/-coaching/-flashcards.js);
 * `payload` (ör. `{learningTab}`, `{coachingTab}`) bölümün `shown`'ına geçer.
 * Kurumda kapalı özelliğin bölümü (bilgi kartları, kurum cari, kantin) çizilmez.
 */
export function openStudent(app, studentId, { tab = 'overview', payload = null, status = null,
  kmId = null } = {}) {
  const disposers = [];
  let detail = null;
  const recordable = typeof app.capability?.('bbd_students.record') === 'function';
  // Durum listeden biliniyorsa sekmeler hemen kurulur; bilinmiyorsa (başka
  // bölümden açılış) başlık gelene kadar beklenir — pasif öğrencide yazma
  // düğmesi bir an bile açık çizilmesin.
  let locked = status === 'inactive';
  const known = Boolean(status);
  const view = readOnlyApp(app, () => locked);
  // KM kaydı bu kartın "Kayıt ve veliler" sekmesidir; yetenek yoksa (modül
  // kapalı / izin yok) eski davranış: KM Öğrenci Yönetimi ekranı açılır.
  const km = recordable
    ? button('Kayıt ve veliler', { variant: 'ghost', onClick: () => select('record') })
    : button('KM Öğrenciler\'de aç', { variant: 'ghost',
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
    app: view,
    id: studentId,
    ready,
    get student() { return detail; },
    subjectNames: new Map(),
    dispose: (fn) => disposers.push(fn),
    select: (key, payload) => select(key, payload),
    /** KM kaydı bu kartta mı (`Kayıt ve veliler` sekmesi)? */
    recordable,
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
    const student = data.student || {};
    const wasLocked = locked;
    locked = student.status === 'inactive';
    markReady(data);
    // Liste ile açılış arasında durum değiştiyse kurulmuş sekmeler yeniden çizilir.
    if (known && wasLocked !== locked) queueMicrotask(() => resetTabs());
    panel.setTitle(student.displayName || 'Öğrenci');
    if (subtitle) {
      subtitle.textContent = [student.externalRef, student.classGroup?.name,
        data.schoolNo ? `okul no ${data.schoolNo}` : ''].filter(Boolean).join(' · ');
    }
    return renderHeader(ctx, data);
  }, { rows: 4, columns: 4 });

  // Kart: 5 sekme (`sections.js` → STUDENT_CARD), sekme içinde katlanır
  // bölümler. Bölüm anahtarları eski sekme anahtarlarıdır; gövde kurucuları aynı.
  const builders = {
    overview: overviewTab,
    goals: embedded(goalsPanel),
    schedule: scheduleTab,
    attendance: attendanceTab,
    requests: requestsTab,
    homework: homeworkTab,
    learning: embedded(learningPanel),
    study: studyTab,
    flashcards: embedded(flashcardsPanel),
    exams: examsTab,
    curriculum: curriculumTab,
    resources: resourcesTab,
    coaching: embedded(coachingPanel),
    finance: financeTab,
    canteen: canteenTab,
    record: () => kmRecordPart(),
  };

  /** "Kayıt ve veliler": KM kaydı (`bbd_students.record`); kantin kimliği başlıktan gelir. */
  function kmRecordPart() {
    const node = h('div', 'ak-stu-record');
    let instance = null;
    let started = false;
    const draw = (ref) => {
      if (!ref) {
        node.replaceChildren(empty('KM kaydı bulunamadı', 'Bu öğrencinin Kontrol Merkezi kimliği yok; '
          + 'KM eşitlemesi tamamlanınca burada görünür.'));
        return;
      }
      instance = app.capability('bbd_students.record')(node, {
        kantinId: ref, capability: app.capability, dialogRoot: app.root,
        toast: (text) => app.toast(text, 'good'),
        // "Akademi'ye ulaştı ✓": geçidin teslim imleci (Ayarlar → Senkron ile aynı veri).
        syncProbe: () => app.api.km('/events'),
        // Ad, sınıf, durum değişince kartın başlığı da Akademi'den yeniden okunur
        // (Akademi değişikliği olayla birkaç saniyede alır).
        onChanged: () => setTimeout(() => head.reload(), 1500),
        onRemoved: () => panel.close(),
      });
    };
    return {
      node,
      shown() {
        if (started) return;
        started = true;
        if (kmId) draw(kmId);
        else ready.then((data) => draw(data?.student?.externalRef || null));
      },
      destroy() { instance?.destroy(); },
    };
  }
  const layoutTabs = studentCard({ has: (permission) => app.has(permission),
    features: { flashcards: app.enabled('flashcards'), finance: app.enabled('finance'),
      canteen: app.enabled('canteen') },
    capabilities: { record: recordable } });
  const start = locatePart(layoutTabs, tab);
  const tabsBar = tabBar(layoutTabs.map(({ key, label }) => ({ key, label })), start.tab,
    (key) => show(key));
  const slot = h('div', 'ak-stu-tabbody');
  const groups = {};                  // sekme → {node, folds: Map(bölüm → kat)}

  // Bölüm gövdesi `{node, focus?, shown?, destroy?}`: kat ilk açıldığında
  // kurulur (ağ o an başlar), `shown` her açılışta, `destroy` çekmece kapanınca.
  function foldOf(part) {
    const node = h('details', 'ak-fold');
    const slotNode = h('div', 'ak-fold-body');
    node.append(h('summary', '', part.label), slotNode);
    const entry = { node, slot: slotNode, part, body: null, pending: null };
    node.addEventListener('toggle', () => {
      if (!node.open) return;
      const payload = entry.pending;
      entry.pending = null;
      openPart(entry, payload);
    });
    return entry;
  }

  function openPart(entry, payload) {
    if (!entry.body) {
      const body = builders[entry.part.key](ctx);
      entry.body = body;
      entry.slot.append(body.node);
      if (body.destroy) disposers.push(() => body.destroy());
    }
    entry.body.shown?.(payload);
    if (payload) entry.body.focus?.(payload);
  }

  /** Katı aç (kapalıysa `toggle` olayı kurar) ve görünür kıl. */
  function openFold(entry, payload) {
    if (entry.node.open) {
      openPart(entry, payload);
    } else {
      entry.pending = payload;
      entry.node.open = true;
    }
    queueMicrotask(() => entry.node.scrollIntoView?.({ block: 'nearest' }));
  }

  /** Tek bölümlü sekme: katlanmaz, bölüm doğrudan çizilir (her zaman "açık"). */
  function plainOf(part) {
    const node = h('div', 'ak-fold-plain');
    const entry = { node, slot: node, part, body: null, pending: null };
    Object.defineProperty(node, 'open', { get: () => true, set: () => {} });
    return entry;
  }

  function groupOf(key) {
    if (!groups[key]) {
      const spec = layoutTabs.find((item) => item.key === key);
      const node = h('div', 'ak-stu-group');
      const single = spec.parts.length === 1;
      const folds = new Map(spec.parts.map((part) => [part.key, single ? plainOf(part) : foldOf(part)]));
      node.append(...[...folds.values()].map((entry) => entry.node));
      groups[key] = { node, folds, spec, opened: false };
      slot.append(node);
    }
    return groups[key];
  }

  /** Sekmeyi göster; `part` verilirse o bölümü aç. */
  function show(key, part = null, payload = null) {
    current = key;
    const group = groupOf(key);
    for (const [name, item] of Object.entries(groups)) item.node.hidden = name !== key;
    const target = part && group.folds.has(part) ? part : null;
    if (!group.opened) {
      // İlk gösterim: hedef bölüm ya da sekmenin ilk bölümü açık gelir.
      group.opened = true;
      openFold(group.folds.get(target || group.spec.parts[0].key), target ? payload : null);
    } else if (target) {
      openFold(group.folds.get(target), payload);
    } else {
      // Sekmeye geri dönüldü: açık bölümler `shown` alır (kendi tazeleme kuralları).
      for (const entry of group.folds.values()) {
        if (entry.node.open && entry.body) entry.body.shown?.(null);
      }
    }
  }

  let current = start.tab;
  function resetTabs() {
    for (const [name, group] of Object.entries(groups)) {
      for (const entry of group.folds.values()) {
        try { entry.body?.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
      group.node.remove();
      delete groups[name];
    }
    show(current);
  }

  /** Kart içi geçiş: sekme ya da bölüm (eski sekme adı) anahtarı. */
  function select(key, payload) {
    const known = layoutTabs.some((item) => item.key === key
      || item.parts.some((part) => part.key === key));
    if (!known) return;
    const place = locatePart(layoutTabs, key);
    tabsBar.select(place.tab, false);
    show(place.tab, place.tab === key ? null : place.part, payload);
  }

  panel.body.append(head.node, tabsBar.node, slot);
  head.reload();
  const opening = () => {
    show(start.tab, start.part, start.part === tab ? payload : null);
    // Aramadan / listeden "Kayıt ve veliler" ile açılınca düzenleme alanı hemen
    // görünsün (başlık kartı uzun): sekme şeridi çekmecenin tepesine gelir.
    if (start.tab === 'record') queueMicrotask(() => tabsBar.node.scrollIntoView?.({ block: 'start' }));
  };
  if (known) opening();
  else ready.then(opening);
  return { close: panel.close, select };
}

// ------------------------------------------- KM kimliğiyle açılış (yönetim)

/**
 * KM öğrenci kimliğiyle (`STU-…`) TEK KARTI açar: Akademi'deki karşılığı
 * (`admin_listStudents?externalRef=`) bulunursa tam kart, bulunamazsa (yeni
 * eklendi, eşitleme sürüyor) yalnız KM kaydıyla açılır ve sebebi yazar.
 * Önce etkin + bulunamayanlar, sonra pasifler aranır.
 */
export async function openStudentByRef(app, kmId, { tab = 'record', name = '', status = null } = {}) {
  let found = null;
  let note = '';
  // Akademi izni yok (0.5.3): kart yalnız KM kaydıdır; Akademi'ye istek gitmez.
  if (app.kmOnly?.()) return openKmOnly(app, kmId, { name, note: '' });
  try {
    for (const filter of [undefined, 'inactive']) {
      const data = await app.api.get('/v1/admin/students', { externalRef: kmId, status: filter, limit: 5 });
      found = (data?.items || []).find((row) => row.externalRef === kmId) || null;
      if (found) break;
    }
  } catch (failure) {
    note = `BBD Akademi bilgileri şu an okunamadı: ${problem(failure)}`;
  }
  if (found) return openStudent(app, found.id, { tab, status: found.status || status, kmId });
  return openKmOnly(app, kmId, { name, note: note || 'Bu öğrenci BBD Akademi\'ye henüz eşitlenmedi; '
    + 'ders, ödev ve deneme bilgileri eşitleme tamamlanınca (genellikle birkaç saniye) bu kartta görünür.' });
}

/** Akademi karşılığı olmayan öğrenci: yalnız KM kaydı (kimlik, sınıf, veliler, kantin). */
function openKmOnly(app, kmId, { name = '', note = '' } = {}) {
  const drawRecord = app.capability?.('bbd_students.record');
  let instance = null;
  const panel = openDrawer(app, { title: name || 'Öğrenci', subtitle: 'Kayıt ve veliler',
    onClose: () => instance?.destroy() });
  panel.node.classList.add('ak-stu-drawer');
  const info = h('div', 'kit-hint ak-stu-hint');
  info.append(h('span', '', note));
  const retry = button('Tekrar dene', { variant: 'ghost', onClick: () => {
    panel.close();
    openStudentByRef(app, kmId, { name });
  } });
  info.append(retry);
  const slot = h('div', 'ak-stu-record');
  if (note) panel.body.append(info);
  panel.body.append(slot);
  if (typeof drawRecord === 'function') {
    instance = drawRecord(slot, { kantinId: kmId, capability: app.capability, dialogRoot: app.root,
      toast: (text) => app.toast(text, 'good'), onRemoved: () => panel.close(),
      syncProbe: app.kmOnly?.() ? null : () => app.api.km('/events'),
      onLoaded: (student) => panel.setTitle(student?.displayName || name || 'Öğrenci') });
  } else {
    slot.append(empty('Kayıt gösterilemiyor', 'Öğrenci yönetimi bu kullanıcı için açık değil.'));
  }
  return { close: panel.close, select() {} };
}

// ------------------------------------------------------- pasif: salt okunur

/** Pasif öğrencide de açık kalan yazmalar: kurum cari (borç tahsil edilebilmeli) ve dışa aktarım. */
const OPEN_WHEN_INACTIVE = /export|report|pdf|statement|receipt/i;

/**
 * Pasif öğrencinin çekmecesi için uygulama görünümü. `locked()` doğruyken
 * okuma (GET) ve kurum cari (`finance.*`) dışındaki işlemler gerekçeli kapalı
 * çizilir (`actionButton` → `app.api.can`). Asıl kapı Akademi'dedir; bu yalnız
 * kullanıcıya yapamayacağı düğmeyi göstermemek içindir (K9).
 */
export function readOnlyApp(app, locked) {
  const open = (operationId) => {
    if (!locked()) return true;
    const operation = app.api.operation(operationId);
    if (!operation) return true;                 // liste okunamadı → karar Akademi'de
    if (operation.method === 'GET' || OPEN_WHEN_INACTIVE.test(operationId)) return true;
    return (operation.akademiPermissions || []).some((key) => String(key).startsWith('finance.'));
  };
  const api = { ...app.api, can: (operationId) => app.api.can(operationId) && open(operationId) };
  return { ...app, api, can: (operationId) => api.can(operationId), readOnly: () => locked() };
}
