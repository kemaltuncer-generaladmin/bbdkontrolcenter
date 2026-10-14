// BBD Akademi — Öğrenciler › Sınıflar (Akademi eşlemesi + sınıf müfredatı).
//
// SINIF = GRUP (kullanıcı kararı 14.10.2026, README §14 madde 68): tek kavram "Sınıf". KM'deki
// eski sınıflar ve eski "gruplar" Akademi'de TEK listededir (`admin_listClassGroups`); bir öğrenci
// birden çok sınıfa eşit üyedir. Ad, anons adı, üyeler ve arşiv KM'nindir (Öğrenciler → Sınıflar).
// Akademi'de yalnız Akademi'ye ait eşleme düzenlenir (`admin_patchClassGroup`: görünen ad, dönem,
// sorumlu öğretmen, etkinlik) ve SINIF MÜFREDATI atanır (`admin_changeClassGroupCurricula` — ör.
// Hüseyin Hoca'nın TYT sınıfına "TYT Matematik"; üyelerin etkin setine eklenir).
//
// Liste küçük ve seyrek değişir: önbellekli okunur (`getCached`), her yazmadan sonra önbellek
// düşürülür (`invalidate`) — bölümlerin sınıf seçicileri (`app.lookups`) aynı önbelleği paylaşır.

import { button, foldText, h } from '../../ui-kit/kit.js';
import { badge, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  EXAM_TARGETS, TRACKS, actionButton, empty, formDialog, gradeText, kv, loader, num,
  openDrawer, searchBox, toolbar,
} from './common.js';
import { STUDENT_STATUS, checkbox, stack } from './students-detail.js';
import { classCurriculumSet, curriculaChips } from './curriculum-set.js';
import { classMembersCard } from './students-class-history.js';

const CLASS_CACHE = '/v1/admin/class-groups';

function studentColumns() {
  return [
    { key: 'name', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => stack(row.displayName,
      [row.externalRef, row.classGroup?.name].filter(Boolean).join(' · ')) },
    { key: 'grade', label: 'Seviye', cell: (row) => gradeText(row.gradeLevel) },
    { key: 'track', label: 'Alan', cell: (row) => TRACKS[row.track] || '—' },
    { key: 'target', label: 'Hedef', cell: (row) => EXAM_TARGETS[row.examTarget] || '—' },
    { key: 'status', label: 'Durum', cell: (row) => (row.status === 'missing'
      ? badge(STUDENT_STATUS.missing[0], 'warn') : '—') },
  ];
}

// ------------------------------------------------------------------ sınıflar

export function classesView(app, { openStudent }) {
  const node = h('div', 'ak-stack');
  let showInactive = false;
  let showArchived = false;
  let filter = '';
  let rows = [];
  let termNames = new Map();
  const search = searchBox({ placeholder: 'Sınıf ara', onSearch: (q) => { filter = foldText(q); paint(); } });
  node.append(toolbar(search.node, checkbox('Pasif sınıfları da göster', (on) => { showInactive = on; view.reload(); }),
    checkbox('Arşivdekileri de göster', (on) => { showArchived = on; view.reload(); }),
    h('span', 'kit-spacer'), h('span', 'ak-muted', 'Sınıflar KM\'de açılır (ad, üyeler, arşiv); burada dönem, '
      + 'sorumlu öğretmen ve sınıf müfredatı eşlenir.')));

  const grid = dataTable({
    columns: [
      { key: 'name', label: 'Sınıf', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(stack(row.name, row.kmClassName && row.kmClassName !== row.name ? `KM: ${row.kmClassName}` : ''));
        if (row.lessonKind === 'individual') cell.append(badge('Özel ders', 'info'));
        if (row.archived) cell.append(badge('Arşiv', 'dim'));
        return cell;
      } },
      // Seviye KM sınıf tanımından (şablon; öğrencinin seviyesi öğrencidedir). Seviyesiz sınıf
      // (ör. "TYT Matematik") olağandır — uyarı yalnız addan tahminde.
      { key: 'grade', label: 'Seviye', cell: (row) => {
        if (row.gradeLevel === null || row.gradeLevel === undefined) return '—';
        const text = [gradeText(row.gradeLevel), row.examTarget].filter(Boolean).join(' · ');
        return row.levelSource === 'name' ? stack(text, 'addan tahmin — Sınıflar\'da seçin') : text;
      } },
      { key: 'track', label: 'Alan', cell: (row) => TRACKS[row.track] || '—' },
      { key: 'term', label: 'Dönem', cell: (row) => (row.termId ? termNames.get(row.termId) || '—'
        : badge('Atanmamış', 'warn')) },
      { key: 'teacher', label: 'Sorumlu öğretmen', width: 'minmax(0, 1.4fr)',
        cell: (row) => row.homeroomTeacher?.displayName || '—' },
      { key: 'curricula', label: 'Sınıf müfredatı', width: 'minmax(0, 1.6fr)', cell: (row) => curriculaChips(row.curricula) },
      { key: 'count', label: 'Öğrenci', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'active', label: 'Durum', cell: (row) => (row.active ? badge('Aktif', 'good') : badge('Pasif', 'dim')) },
    ],
    rows: [],
    onRow: (row) => openClass(row.id),
    empty: empty('Sınıf yok', 'Sınıflar KM → Öğrenciler → Sınıflar ekranında açılır ve buraya eşitlenir.'),
  });

  const view = loader(async () => {
    const [data, terms] = await Promise.all([
      app.api.getCached(CLASS_CACHE, { active: showInactive ? undefined : true,
        archived: showArchived ? true : undefined }),
      app.lookups.terms().catch(() => []),
    ]);
    termNames = new Map(terms.map((term) => [term.id, term.name]));
    return data?.items || [];
  }, (items) => {
    rows = [...items].sort((a, b) => String(a.name).localeCompare(String(b.name), 'tr'));
    paint();
    return grid.node;
  }, { rows: 6, columns: 6 });
  node.append(view.node);

  function paint() {
    grid.update({ rows: filter
      ? rows.filter((row) => foldText(`${row.name} ${row.kmClassName}`).includes(filter)) : rows });
  }

  function openClass(classGroupId) {
    let members = null;
    const panel = openDrawer(app, { title: 'Sınıf', subtitle: 'Akademi eşlemesi, sınıf müfredatı ve üyelik geçmişi',
      onClose: () => members?.destroy() });
    let curricula = null;
    panel.node.classList.add('ak-stu-sub');
    const detail = loader(async () => {
      const [group, terms] = await Promise.all([
        app.api.get(`/v1/admin/class-groups/${classGroupId}`),
        app.lookups.terms().catch(() => []),
      ]);
      return { group, terms };
    }, ({ group, terms }) => {
      panel.setTitle(group.name);
      const term = terms.find((item) => item.id === group.termId);
      const students = dataTable({
        columns: studentColumns(),
        rows: [...(group.students || [])].sort((a, b) => String(a.displayName).localeCompare(String(b.displayName), 'tr')),
        onRow: (student) => openStudent(student.id),
        empty: empty('Bu sınıfta öğrenci yok'),
      });
      return [
        toolbar(actionButton(app, 'admin_patchClassGroup', 'Eşlemeyi düzenle', { variant: 'primary',
          onClick: () => edit(group, terms) }),
        button('Ders programı', { variant: 'ghost', onClick: () => {
          panel.close();
          app.go('timetable', { classGroupId: group.id });
        } })),
        card('', kv([
          ['Akademi adı', group.name],
          ['KM adı', group.kmClassName],
          ['Anons adı', group.callName || '—'],
          ['Seviye', gradeText(group.gradeLevel)],
          ['Alan', TRACKS[group.track] || '—'],
          ['Dönem', term ? `${term.name}${term.current ? ' (güncel)' : ''}` : 'Atanmamış'],
          ['Sorumlu öğretmen', group.homeroomTeacher?.displayName || 'Atanmamış'],
          ['Öğrenci', num(group.studentCount)],
          ['Durum', group.archived ? badge('Arşiv (KM)', 'dim')
            : group.active ? badge('Aktif', 'good') : badge('Pasif', 'dim')],
        ])),
        curriculaCard(group),
        card(`Öğrenciler (${num((group.students || []).length)})`, students.node, 'Satıra tıklayınca öğrenci detayı açılır'),
        membersCard(group),
      ].filter(Boolean);
    }, { rows: 6, columns: 4 });
    panel.body.append(detail.node);
    detail.reload();

    /** Tarihli üyelikler (F14): kim, hangi günden hangi güne bu sınıftaydı. */
    function membersCard(group) {
      members?.destroy();
      members = classMembersCard(app, group.id, { openStudent });
      return members.node;
    }

    /** Sınıf müfredatı (F-C7): üyelerin etkin setine eklenir. Eski Akademi'de işlem yoksa gizli. */
    function curriculaCard(group) {
      if (!app.can('admin_getClassGroupCurricula')) return null;
      curricula?.destroy();
      curricula = classCurriculumSet(app, group.id, { archived: Boolean(group.archived),
        onChanged: () => view.reload() });
      curricula.reload();
      return card('Sınıf müfredatı', curricula.node, 'Ör. TYT sınıfına “TYT Matematik”');
    }

    async function edit(group, terms) {
      const saved = await editClass(app, group, terms);
      if (!saved) return;
      app.toast('Sınıf eşlemesi kaydedildi.', 'good');
      app.api.invalidate(CLASS_CACHE);
      detail.reload();
      view.reload();
    }
  }

  return { node, reload: () => view.reload(), openClass, destroy() { search.destroy(); } };
}

async function editClass(app, group, terms) {
  const teachers = await app.lookups.teachers().catch(() => []);
  const teacherOptions = [{ value: '', label: 'Atanmamış' },
    ...teachers.map((teacher) => ({ value: teacher.id, label: teacher.displayName }))];
  const current = group.homeroomTeacher;
  if (current && !teachers.some((teacher) => teacher.id === current.id)) {
    teacherOptions.push({ value: current.id, label: current.displayName });
  }
  const termOptions = [{ value: '', label: 'Dönem atanmamış' },
    ...terms.map((term) => ({ value: term.id, label: `${term.name}${term.current ? ' (güncel)' : ''}` }))];
  return formDialog(app, {
    title: `${group.name}: Akademi eşlemesi`,
    description: `KM adı: ${group.kmClassName}. Ad, üyeler ve arşiv KM'den gelir; burada yalnız `
      + 'Akademi\'ye ait eşleme düzenlenir.',
    value: {
      displayName: group.name !== group.kmClassName ? group.name : '',
      termId: group.termId || '',
      homeroomTeacherId: current?.id || '',
      active: group.active !== false,
    },
    fields: [
      { key: 'displayName', label: 'Görünen ad', maxLength: 60, placeholder: group.kmClassName,
        hint: 'Boş bırakılırsa KM\'deki sınıf adı kullanılır.' },
      { key: 'termId', label: 'Dönem', type: 'select', options: termOptions },
      { key: 'homeroomTeacherId', label: 'Sorumlu öğretmen', type: 'select', options: teacherOptions },
      { key: 'active', label: 'Aktif (seçicilerde ve listelerde görünür)', type: 'checkbox' },
    ],
    onSubmit: (draft) => app.api.patch(`/v1/admin/class-groups/${group.id}`, {
      displayName: String(draft.displayName || '').trim() || null,
      termId: draft.termId || null,
      homeroomTeacherId: draft.homeroomTeacherId || null,
      active: Boolean(draft.active),
    }),
  });
}
