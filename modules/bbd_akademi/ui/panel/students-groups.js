// BBD Akademi — Öğrenciler › Sınıflar ve Çalışma grupları.
//
// SINIF KM'DEN GELİR. Sınıf grupları KM sınıflarının aynasıdır: ad, seviye, alan
// ve öğrenciler KM'de tanımlanır. Akademi'de yalnız Akademi'ye ait eşleme
// düzenlenir (`admin_patchClassGroup`): görünen ad, dönem, rehber öğretmen,
// etkinlik.
//
// ÇALIŞMA GRUBU DA KM'NİNDİR (ürün kararı 12.10.2026). Sınıftan bağımsız öğrenci
// kümesi (ör. "TYT hızlandırma") KM öğrenci yönetiminde açılır, Akademi'ye
// eşitlenir; burada salt okunur listelenir (atamalarda hedef, süzgeç).
//
// İki liste de küçük ve seyrek değişir: önbellekli okunur (`getCached`), her
// yazmadan sonra önbellek düşürülür (`invalidate`) — bölümlerin sınıf/grup
// seçicileri (`app.lookups`) aynı önbelleği paylaşır.

import { button, foldText, h } from '../../ui-kit/kit.js';
import { badge, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  EXAM_TARGETS, TRACKS, actionButton, empty, formDialog, gradeText, kv, loader, num,
  openDrawer, searchBox, toolbar,
} from './common.js';
import { STUDENT_STATUS, checkbox, stack } from './students-detail.js';

const CLASS_CACHE = '/v1/admin/class-groups';
const GROUP_CACHE = '/v1/admin/student-groups';

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
  let filter = '';
  let rows = [];
  let termNames = new Map();
  const search = searchBox({ placeholder: 'Sınıf ara', onSearch: (q) => { filter = foldText(q); paint(); } });
  node.append(toolbar(search.node, checkbox('Pasif sınıfları da göster', (on) => { showInactive = on; view.reload(); }),
    h('span', 'kit-spacer'), h('span', 'ak-muted', 'Sınıflar KM\'den eşitlenir; burada dönem ve rehber öğretmen eşlenir.')));

  const grid = dataTable({
    columns: [
      { key: 'name', label: 'Sınıf', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.name,
        row.kmClassName && row.kmClassName !== row.name ? `KM: ${row.kmClassName}` : '') },
      { key: 'grade', label: 'Seviye', cell: (row) => gradeText(row.gradeLevel) },
      { key: 'track', label: 'Alan', cell: (row) => TRACKS[row.track] || '—' },
      { key: 'term', label: 'Dönem', cell: (row) => (row.termId ? termNames.get(row.termId) || '—'
        : badge('Atanmamış', 'warn')) },
      { key: 'teacher', label: 'Rehber öğretmen', width: 'minmax(0, 1.4fr)',
        cell: (row) => row.homeroomTeacher?.displayName || '—' },
      { key: 'count', label: 'Öğrenci', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'active', label: 'Durum', cell: (row) => (row.active ? badge('Aktif', 'good') : badge('Pasif', 'dim')) },
    ],
    rows: [],
    onRow: (row) => openClass(row.id),
    empty: empty('Sınıf yok', 'Sınıflar KM\'nin Öğrenciler modülünden eşitlenir.'),
  });

  const view = loader(async () => {
    const [data, terms] = await Promise.all([
      app.api.getCached(CLASS_CACHE, showInactive ? undefined : { active: true }),
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
    const panel = openDrawer(app, { title: 'Sınıf', subtitle: 'KM sınıfının Akademi eşlemesi' });
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
          ['KM sınıfı', group.kmClassName],
          ['Seviye', gradeText(group.gradeLevel)],
          ['Alan', TRACKS[group.track] || '—'],
          ['Dönem', term ? `${term.name}${term.current ? ' (güncel)' : ''}` : 'Atanmamış'],
          ['Rehber öğretmen', group.homeroomTeacher?.displayName || 'Atanmamış'],
          ['Öğrenci', num(group.studentCount)],
          ['Durum', group.active ? badge('Aktif', 'good') : badge('Pasif', 'dim')],
        ])),
        card(`Öğrenciler (${num((group.students || []).length)})`, students.node, 'Satıra tıklayınca öğrenci detayı açılır'),
      ];
    }, { rows: 6, columns: 4 });
    panel.body.append(detail.node);
    detail.reload();

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
    description: `KM sınıfı: ${group.kmClassName}. Seviye, alan ve öğrenciler KM'den gelir; burada yalnız `
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
      { key: 'homeroomTeacherId', label: 'Rehber öğretmen', type: 'select', options: teacherOptions },
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

// ---------------------------------------------------------- çalışma grupları

/**
 * Çalışma grupları — SALT OKUNUR. Ürün kararı (12.10.2026): gruplar KM'nin
 * kendi öğrenci yönetiminde (Öğrenciler modülü: grup adı + öğrenci seçimi,
 * bir öğrenci birden çok grupta) tutulur ve Akademi'ye eşitlenir; Akademi
 * grup açmaz. Akademi'nin grup yazma uçları bu yüzden panelde yok
 * (ui_coverage NOT_SURFACED).
 */
export function groupsView(app, { openStudent } = {}) {
  const node = h('div', 'ak-stack');
  const hint = h('div', 'kit-hint ak-stu-hint');
  hint.append(h('span', '', 'Çalışma grupları KM öğrenci yönetiminden yönetilir ve Akademi\'ye eşitlenir: '
    + 'grubu açmak, adını değiştirmek ya da öğrenci eklemek için Öğrenci Yönetimi\'ni kullanın. '
    + 'Burada gruplar atamalarda hedef, öğrenci listesinde süzgeç olarak görünür.'),
  // `{tab: 'groups'}`: Öğrenciler panelinin Gruplar sekmesi (sözleşme §7.A.6);
  // sekmeyi tanımayan sürüm yükü yok sayar ve ana listeyi açar.
  button('Grupları Öğrenci Yönetimi\'nde düzenle', { onClick: () => app.openPanel('bbd_students', { tab: 'groups' }) }));
  node.append(hint);

  const grid = dataTable({
    columns: [
      { key: 'name', label: 'Grup', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.name, row.description) },
      { key: 'count', label: 'Öğrenci', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'members', label: 'Üyeler', width: 'minmax(0, 2fr)', cell: (row) => {
        const names = (row.students || []).map((student) => student.displayName);
        if (!names.length) return '—';
        return `${names.slice(0, 3).join(', ')}${names.length > 3 ? ` +${num(names.length - 3)}` : ''}`;
      } },
    ],
    rows: [],
    onRow: (row) => openGroup(row),
    empty: empty('Çalışma grubu yok', 'Gruplar KM Öğrenciler modülünde açılır ve buraya eşitlenir.'),
  });
  const view = loader(() => app.api.getCached(GROUP_CACHE), (data) => {
    grid.update({ rows: [...(data?.items || [])].sort((a, b) => String(a.name).localeCompare(String(b.name), 'tr')) });
    return grid.node;
  }, { rows: 5, columns: 3 });
  node.append(view.node);

  function openGroup(group) {
    const panel = openDrawer(app, { title: group.name, subtitle: `${num(group.studentCount)} öğrenci · KM'den eşitlenir` });
    panel.node.classList.add('ak-stu-sub');
    const members = dataTable({
      columns: studentColumns(),
      rows: [...(group.students || [])].sort((a, b) => String(a.displayName).localeCompare(String(b.displayName), 'tr')),
      onRow: (student) => openStudent(student.id),
      empty: empty('Grupta öğrenci yok', 'Üyeler KM Öğrenciler modülünde seçilir.'),
    });
    panel.body.append(...[group.description ? h('p', 'ak-muted', group.description) : null,
      card('Üyeler', members.node)].filter(Boolean));
  }

  return { node, reload: () => view.reload() };
}
