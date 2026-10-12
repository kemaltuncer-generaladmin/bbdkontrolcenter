// BBD Akademi — Ders programı › "Haftayı kopyala" (F-A5).
//
// Kaynak: seçili sınıfın, gösterilen günde geçerli SUNUCUDAKİ programı
// (kaydedilmemiş taslak kopyalanmaz). Hedef başına bağımsızdır: Akademi
// çakışan hedefe yazmaz (`conflict`), diğerlerine kopyalar. "Çakışmaları
// denetle" aynı isteği `dryRun: true` ile gönderir — hiçbir şey yazılmaz.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import { empty, errorBox, fmtDate, formDialog, multiChips, num, openDrawer, todayIst } from './common.js';
import { conflictText } from './timetable-shared.js';

const COPY_STATUS = { copied: ['Kopyalandı', 'good'], would_copy: ['Kopyalanabilir', 'info'],
  conflict: ['Çakışma — yazılmadı', 'bad'] };

/**
 * @param {object} ctx
 *   source {id, name}, sourceDate 'YYYY-MM-DD', defaultFrom, hours (ders saati),
 *   dirty (kaydedilmemiş taslak var mı), classGroups, layout (zil düzeni)
 * @returns {Promise<object|null>} Akademi sonucu `{results}` ya da vazgeçişte null
 */
export async function copyWeekDialog(app, ctx) {
  const { source, sourceDate, hours, dirty, classGroups, layout } = ctx;
  const today = todayIst();
  const options = classGroups.map((item) => ({ value: item.id,
    label: item.id === source.id ? `${item.name} (bu sınıf)` : item.name }));
  const checkSlot = h('div', 'ak-tt-copycheck');

  const range = (draft) => {
    if (!draft.validFrom) return 'Geçerlilik başlangıcını seçin.';
    if (draft.openEnded) return null;
    if (!draft.validTo) return 'Geçerlilik bitişini seçin ya da “Süresiz”i işaretleyin.';
    return draft.validTo < draft.validFrom ? 'Geçerlilik bitişi başlangıçtan önce olamaz.' : null;
  };
  const copyBody = (draft, dryRun) => ({
    sourceClassGroupId: source.id,
    sourceDate,
    targets: (draft.targets || []).map((classGroupId) => ({ classGroupId, validFrom: draft.validFrom,
      validTo: draft.openEnded ? null : (draft.validTo || null) })),
    keepTeachers: Boolean(draft.keepTeachers),
    keepRooms: Boolean(draft.keepRooms),
    dryRun,
  });

  const result = await formDialog(app, {
    title: `${source.name} programını kopyala`,
    description: [
      `Kaynak: ${fmtDate(sourceDate)} gününde geçerli program (${num(hours)} ders saati).`,
      dirty ? 'Kaydedilmemiş değişiklikler kopyalanmaz; önce kaydedin.' : '',
      'Her hedef sınıf ayrı işlenir: çakışan sınıfa yazılmaz, diğerlerine kopyalanır. '
        + 'Hedefin seçilen aralıktaki programı bununla değişir.',
    ].filter(Boolean).join('\n'),
    wide: true,
    value: { validFrom: ctx.defaultFrom && ctx.defaultFrom > today ? ctx.defaultFrom : today, validTo: '',
      openEnded: true, keepTeachers: true, keepRooms: true },
    fields: [
      { key: 'validFrom', label: 'Geçerlilik başlangıcı', type: 'date', required: true },
      { key: 'validTo', label: 'Geçerlilik bitişi', type: 'date',
        hint: '“Süresiz” işaretliyse dikkate alınmaz.' },
      { key: 'openEnded', label: 'Süresiz (bitiş tarihi yok)', type: 'checkbox' },
      { key: 'keepTeachers', label: 'Öğretmenler de kopyalansın', type: 'checkbox' },
      { key: 'keepRooms', label: 'Derslikler de kopyalansın', type: 'checkbox' },
      { key: 'targets', label: 'Hedef sınıflar', type: 'custom', required: true,
        hint: 'Bu sınıfı seçerseniz program aynı sınıfa yeni geçerlilik aralığıyla kopyalanır.',
        render: (draft, set) => multiChips(options, [], set) },
      { key: 'check', label: 'Ön denetim', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Çakışmaları denetle', { onClick: async () => {
          const issue = !(draft.targets || []).length ? 'Önce hedef sınıf seçin.' : range(draft);
          if (issue) {
            checkSlot.replaceChildren(alertBox(issue, 'info'));
            return;
          }
          go.disabled = true;
          try {
            const check = await app.api.post('/v1/admin/timetable/copy', copyBody(draft, true));
            checkSlot.replaceChildren(copyResultsTable(check, { classGroups, layout }));
          } catch (failure) {
            checkSlot.replaceChildren(errorBox(failure));
          } finally {
            go.disabled = false;
          }
        } });
        wrap.append(h('span', 'ak-muted', 'İsteğe bağlı: hiçbir şey yazmadan hangi sınıfta çakışma olacağını gösterir.'),
          go, checkSlot);
        return wrap;
      } },
    ],
    submitLabel: 'Kopyala',
    onSubmit: (draft, { key }) => {
      const issue = range(draft);
      if (issue) throw new AkademiError({ code: 'invalid_range', message: issue });
      return app.api.post('/v1/admin/timetable/copy', copyBody(draft, false), { key });
    },
  });
  if (!result || result === true) return null;

  const results = result.results || [];
  const copied = results.filter((item) => item.status === 'copied').length;
  const skipped = results.filter((item) => item.status === 'conflict').length;
  app.toast(`${num(copied)} sınıfa kopyalandı${skipped ? ` · ${num(skipped)} sınıf çakışma nedeniyle atlandı` : ''}.`,
    skipped ? 'warn' : 'good');
  const panel = openDrawer(app, { title: 'Kopyalama sonucu',
    subtitle: `Kaynak: ${source.name} · ${fmtDate(sourceDate)}` });
  panel.body.append(copyResultsTable(result, { classGroups, layout }));
  return result;
}

/** Hedef başına sonuç tablosu (ön denetim ve gerçek kopya aynı biçim). */
export function copyResultsTable(result, { classGroups, layout }) {
  const names = new Map((classGroups || []).map((item) => [item.id, item.name]));
  const table = dataTable({
    columns: [
      { key: 'classGroup', label: 'Sınıf', width: '140px', cell: (row) => names.get(row.classGroupId) || row.classGroupId },
      { key: 'status', label: 'Sonuç', width: '170px', cell: (row) => {
        const [text, tone] = COPY_STATUS[row.status] || [row.status, 'dim'];
        return badge(text, tone);
      } },
      { key: 'detail', label: 'Ayrıntı', width: 'minmax(0, 1fr)', className: 'ak-tt-wrap', cell: (row) => {
        const conflicts = row.conflicts || [];
        if (!conflicts.length) return row.status === 'conflict' ? 'Çakışma' : '—';
        const cell = h('div', 'ak-cell-stack');
        for (const item of conflicts) cell.append(h('small', '', conflictText(item, layout)));
        return cell;
      } },
    ],
    rows: result?.results || [],
    rowKey: (row) => row.classGroupId,
    empty: empty('Sonuç yok'),
  });
  return table.node;
}
