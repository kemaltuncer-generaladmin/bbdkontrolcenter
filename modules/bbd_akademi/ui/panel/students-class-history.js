// BBD Akademi — tarihli sınıf üyelikleri (F14; Akademi F-Y1).
//
// Sınıflar ve üyelikleri KM'nindir (Öğrenciler → Sınıflar); Akademi aynasıdır ve yazmaz. Üyelik
// TARİHLİDİR (`validFrom`–`validTo`, ikisi de dahil; `validTo` boş = sürüyor). Bu dosya iki salt
// okunur görünüm verir:
//   · sınıf çekmecesinde "Üyelik geçmişi" — `admin_listGroupMembers` (imleçli; gün seçilirse o gün
//     geçerli üyeler, seçilmezse bütün geçmiş, en yeni önce);
//   · öğrenci kartında "Sınıflar ve geçmişi" — `admin_listStudentGroupHistory`; satıra tıklayınca o
//     tarihte sınıfta kimlerin olduğu `admin_getStudentGroup?date=` ile açılır.
// İç tür (`km_class` / `km_group`) kullanıcıya gösterilmez — tek kavram "Sınıf" (README §14 madde 70).

import { button, h } from '../../ui-kit/kit.js';
import { badge, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  dateInput, empty, fmtDate, loader, num, openDrawer, pagedList, staticList, todayIst, toolbar,
} from './common.js';

function stack(main, sub) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('span', '', main || '—'));
  if (sub) node.append(h('small', '', sub));
  return node;
}

/** "01.09.2026 – sürüyor". */
export function rangeText(row) {
  return `${fmtDate(row.validFrom)} – ${row.validTo ? fmtDate(row.validTo) : 'sürüyor'}`;
}

/** Sınıf çekmecesi: tarihli üyelikler. `openStudent(id)` satır tıklaması. */
export function classMembersCard(app, classGroupId, { openStudent } = {}) {
  if (!app.can('admin_listGroupMembers')) return { node: null, destroy() {} };
  let date = '';
  const day = dateInput('', (iso) => { date = iso || ''; list.reload(); }, 'Bu gündeki üyeler');
  const list = pagedList({
    app,
    path: `/v1/admin/class-groups/${classGroupId}/members`,
    query: () => ({ date: date || undefined }),
    limit: 50,
    dense: true,
    rowKey: (row) => `${row.student?.id}|${row.validFrom}`,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.8fr)', cell: (row) => {
        const cell = stack(row.student?.displayName, row.student?.externalRef);
        if (row.pendingStudent) cell.append(badge('KM dizininde henüz yok', 'warn'));
        return cell;
      } },
      { key: 'from', label: 'Başlangıç', cell: (row) => fmtDate(row.validFrom) },
      { key: 'to', label: 'Bitiş', cell: (row) => (row.validTo ? fmtDate(row.validTo) : badge('Sürüyor', 'good')) },
    ],
    onRow: (row) => { if (row.student?.id && !row.pendingStudent) openStudent?.(row.student.id); },
    empty: { title: 'Üyelik yok', text: 'Bu sınıfa KM\'de öğrenci eklenince burada tarihleriyle görünür.' },
  });
  const node = h('div', 'ak-stack');
  node.append(toolbar(day.node, button('Bütün geçmiş', { variant: 'ghost', onClick: () => {
    date = '';
    day.set('');
    list.reload();
  } }), button('Bugün', { variant: 'ghost', onClick: () => { date = todayIst(); day.set(date); list.reload(); } }),
  h('span', 'kit-spacer'), h('span', 'ak-muted', 'Gün seçilmezse bütün geçmiş, en yeni önce.')), list.node);
  list.reload();
  return {
    node: card('Üyelik geçmişi', node, 'Tarihli üyelik; KM\'de yönetilir'),
    destroy() { list.destroy(); day.destroy(); },
  };
}

/** Öğrenci kartı: öğrencinin bütün sınıf üyelikleri, tarihleriyle. */
export function classHistoryCard(ctx) {
  const { app, id } = ctx;
  if (!app.can('admin_listStudentGroupHistory')) return null;
  const list = staticList({
    load: () => app.api.get(`/v1/admin/students/${id}/group-history`),
    dense: true,
    map: (items) => [...items].sort((a, b) => (a.validTo ? 1 : 0) - (b.validTo ? 1 : 0)
      || String(b.validFrom).localeCompare(String(a.validFrom))),
    rowKey: (row) => `${row.group?.id}|${row.validFrom}`,
    columns: [
      { key: 'group', label: 'Sınıf', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(h('span', '', row.group?.name || '—'));
        if (row.group?.kind === 'individual') cell.append(badge('Özel ders', 'info'));
        if (row.archived) cell.append(badge('Arşiv', 'dim'));
        return cell;
      } },
      { key: 'range', label: 'Tarih', width: 'minmax(0, 1.4fr)', cell: (row) => rangeText(row) },
      { key: 'state', label: 'Durum', cell: (row) => (!row.validTo || row.validTo >= todayIst()
        ? badge('Üye', 'good') : badge('Ayrıldı', 'dim')) },
    ],
    onRow: (row) => openGroupAt(app, row),
    empty: { title: 'Sınıf üyeliği yok', text: 'Öğrenci KM\'de bir sınıfa eklenince burada görünür.' },
  });
  list.reload();
  return card('Sınıflar ve geçmişi', list.node, 'Satıra tıklayınca o tarihteki sınıf listesi açılır');
}

/** Üyeliğin son geçerli günündeki (sürüyorsa bugün) sınıf listesi. */
export function openGroupAt(app, membership) {
  const today = todayIst();
  const at = membership.validTo && membership.validTo < today ? membership.validTo : today;
  const panel = openDrawer(app, { title: membership.group?.name || 'Sınıf', subtitle: `${fmtDate(at)} tarihindeki üyeler` });
  const view = loader(() => app.api.get(`/v1/admin/student-groups/${membership.group.id}`, { date: at }), (group) => {
    const students = [...(group.students || [])].sort((a, b) => String(a.displayName).localeCompare(String(b.displayName), 'tr'));
    const table = dataTable({ dense: true, rows: students, columns: [
      { key: 'name', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => stack(row.displayName, row.externalRef) },
    ], empty: empty('Bu tarihte üye yok') });
    return [h('div', 'ak-muted', `${num(group.studentCount ?? students.length)} öğrenci · bu öğrencinin üyeliği ${rangeText(membership)}`
      + `${group.archived ? ' · sınıf arşivde' : ''}`), table.node];
  }, { rows: 6, columns: 1 });
  panel.body.append(view.node);
  view.reload();
  return panel;
}
