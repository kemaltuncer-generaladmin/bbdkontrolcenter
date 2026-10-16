// BBD Akademi — Ders programı › "Haftayı kopyala" (F-A5) ve "Dönem geçişi kopyası" (iş 37 / İ4).
//
// Kaynak: seçili sınıfın, gösterilen günde geçerli SUNUCUDAKİ programı
// (kaydedilmemiş taslak kopyalanmaz). Hedef başına bağımsızdır: Akademi
// çakışan hedefe yazmaz (`conflict`), diğerlerine kopyalar. "Çakışmaları
// denetle" aynı isteği `dryRun: true` ile gönderir — hiçbir şey yazılmaz.
//
// DÖNEM GEÇİŞİ KOPYASI: KM → Öğrenciler → Sınıflar → "Dönem geçişi" eski dönemin
// sınıflarını yeni döneme taşır (7-A → 8-A) ama ders programı ve sınıf müfredatı
// Akademi'dedir. Bu pencere KM'nin son geçişinin eşlemesini okur
// (`GET /api/bbd_students/groups/rollover` → `latest.pairs`, salt okunur `app.kmGet`),
// eşlenen her eski sınıfın programını (`admin_copyTimetable`, kaynak gün = eski
// üyeliklerin son günü) ve sınıf müfredatını (`admin_changeClassGroupCurricula`;
// yalnız hedef dönem güncel dönemken — Akademi müfredatı güncel döneme yazar) yeni
// sınıfa aktarır. Sınıflar Akademi'de ad + dönemle eşlenir; eşitlenmemiş sınıf
// "Akademi'de henüz yok" diye listelenir.

import { button, foldText, h } from '../../ui-kit/kit.js';
import { alertBox, badge } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import { empty, errorBox, fmtDate, formDialog, multiChips, num, openDrawer, todayIst } from './common.js';
import { academicYearOf } from './shared/curriculum-set.js';
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

// ------------------------------------------------------------ dönem geçişi kopyası

const ROLLOVER = '/api/bbd_students/groups/rollover';

/**
 * KM eşlemesini Akademi sınıflarına bağlar: eski sınıf (ad + eski dönem; arşivdeki
 * tercih edilir) → yeni sınıf (ad + yeni dönem, canlı).
 * @returns {Array<{pair, source, target}>}
 */
export function matchRolloverPairs(latest, classGroups) {
  const named = (row) => [foldText(row.kmClassName || ''), foldText(row.name || '')];
  const find = (name, year, { archived }) => {
    const key = foldText(name);
    const hits = (classGroups || []).filter((row) => named(row).includes(key)
      && (!row.academicYear || row.academicYear === year));
    const exact = hits.filter((row) => row.academicYear === year);
    const pool = exact.length ? exact : hits;
    return pool.find((row) => Boolean(row.archived) === archived) || pool[0] || null;
  };
  return (latest?.pairs || []).map((pair) => ({
    pair,
    source: find(pair.fromName, latest.fromYear, { archived: true }),
    target: find(pair.toName, pair.toYear, { archived: false }),
  }));
}

/**
 * Son dönem geçişinin ders programlarını (ve sınıf müfredatını) yeni sınıflara kopyalar.
 * @returns {Promise<object|null>} `{results}` ya da vazgeçişte / geçiş yokken null
 */
export async function rolloverCopyDialog(app, { layout = null } = {}) {
  if (typeof app.kmGet !== 'function') {
    app.toast('KM öğrenci yönetimi bu kurulumda yok.', 'warn');
    return null;
  }
  let latest = null;
  let classGroups = [];
  try {
    const [overview, groups] = await Promise.all([
      app.kmGet(ROLLOVER),
      app.api.get('/v1/admin/class-groups', { archived: true }),
    ]);
    latest = overview?.latest || null;
    classGroups = groups?.items || [];
  } catch (failure) {
    app.toast(`Dönem geçişi okunamadı: ${failure?.message || failure}`, 'bad');
    return null;
  }
  if (!latest || !(latest.pairs || []).length) {
    app.toast('Uygulanmış bir dönem geçişi yok. Önce KM → Öğrenciler → Sınıflar → “Dönem geçişi”.', 'warn');
    return null;
  }
  const matches = matchRolloverPairs(latest, classGroups);
  const ready = matches.filter((item) => item.source && item.target);
  const missing = matches.filter((item) => !item.source || !item.target);
  const today = todayIst();
  const curriculaOpen = academicYearOf(today) === latest.toYear;
  const options = ready.map((item, index) => ({ value: String(index),
    label: `${item.pair.fromName} → ${item.pair.toName}` }));
  const checkSlot = h('div', 'ak-tt-copycheck');
  const names = new Map(classGroups.map((row) => [row.id, row.name]));

  const body = (item, draft, dryRun) => ({
    sourceClassGroupId: item.source.id,
    sourceDate: draft.sourceDate,
    targets: [{ classGroupId: item.target.id, validFrom: draft.validFrom, validTo: null }],
    keepTeachers: Boolean(draft.keepTeachers),
    keepRooms: Boolean(draft.keepRooms),
    dryRun,
  });
  const chosen = (draft) => (draft.pairs || []).map((index) => ready[Number(index)]).filter(Boolean);

  const result = await formDialog(app, {
    title: `Dönem geçişi kopyası · ${latest.fromYear} → ${latest.toYear}`,
    description: [
      `KM geçişi ${fmtDate(latest.effectiveFrom)} gününden: ${num(latest.pairs.length)} sınıf eşlendi.`,
      'Eşlenen her eski sınıfın programı yeni sınıfa kopyalanır; çakışan sınıfa yazılmaz.',
      curriculaOpen ? 'Sınıf müfredatı da yeni sınıfa eklenebilir.'
        : `Sınıf müfredatı ${latest.toYear} dönemi başlayınca (1 Eylül) kopyalanabilir; şimdi yalnız program.`,
      missing.length ? `${num(missing.length)} sınıf Akademi'de henüz yok (eşitleme bekleniyor olabilir): `
        + `${missing.map((item) => item.pair.toName).join(', ')}.` : '',
    ].filter(Boolean).join('\n'),
    wide: true,
    value: { sourceDate: latest.closeDay || latest.effectiveFrom, keepTeachers: true, keepRooms: true,
      validFrom: latest.effectiveFrom > today ? latest.effectiveFrom : today,
      copyCurricula: curriculaOpen, pairs: options.map((option) => option.value) },
    fields: [
      { key: 'sourceDate', label: 'Eski programın günü', type: 'date', required: true,
        hint: 'Eski sınıfların bu günde geçerli programı kopyalanır (varsayılan: eski üyeliklerin son günü).' },
      { key: 'validFrom', label: 'Yeni programın başlangıcı', type: 'date', required: true },
      { key: 'keepTeachers', label: 'Öğretmenler de kopyalansın', type: 'checkbox' },
      { key: 'keepRooms', label: 'Derslikler de kopyalansın', type: 'checkbox' },
      curriculaOpen ? { key: 'copyCurricula', label: 'Sınıf müfredatı da kopyalansın', type: 'checkbox' } : null,
      { key: 'pairs', label: 'Sınıflar', type: 'custom', required: true,
        hint: ready.length ? 'Kopyalanmayacak sınıfın işaretini kaldırın.' : 'Akademi’de eşlenen sınıf yok.',
        render: (draft, set) => multiChips(options, draft.pairs || [], set) },
      { key: 'check', label: 'Ön denetim', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Çakışmaları denetle', { onClick: async () => {
          const picked = chosen(draft);
          if (!picked.length) {
            checkSlot.replaceChildren(alertBox('Önce sınıf seçin.', 'info'));
            return;
          }
          go.disabled = true;
          try {
            const rows = [];
            for (const item of picked) {
              const check = await app.api.post('/v1/admin/timetable/copy', body(item, draft, true)); // eslint-disable-line no-await-in-loop
              rows.push(...(check?.results || []));
            }
            checkSlot.replaceChildren(copyResultsTable({ results: rows }, { classGroups, layout }));
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
    onSubmit: async (draft, { key }) => {
      const picked = chosen(draft);
      if (!picked.length) throw new AkademiError({ code: 'validation_error', message: 'Sınıf seçin.' });
      const results = [];
      for (const [index, item] of picked.entries()) {
        const row = { from: item.source.id, to: item.target.id, program: null, curricula: null, error: null };
        try {
          const copy = await app.api.post('/v1/admin/timetable/copy', body(item, draft, false), // eslint-disable-line no-await-in-loop
            { key: `${key}-p${index}` });
          row.program = copy?.results?.[0] || null;
          if (draft.copyCurricula && curriculaOpen) {
            const set = await app.api.get(`/v1/admin/class-groups/${item.source.id}/curricula`, // eslint-disable-line no-await-in-loop
              { academicYear: latest.fromYear });
            const add = (set?.items || []).map((entry) => entry.program?.id).filter(Boolean);
            if (add.length) {
              await app.api.post(`/v1/admin/class-groups/${item.target.id}/curricula`, { add, remove: [] }, // eslint-disable-line no-await-in-loop
                { key: `${key}-c${index}` });
            }
            row.curricula = add.length;
          }
        } catch (failure) {
          row.error = failure?.message || String(failure);
        }
        results.push(row);
      }
      app.api.invalidate?.('/v1/admin/class-groups');
      return { results };
    },
  });
  if (!result || result === true) return null;

  const copied = result.results.filter((row) => row.program?.status === 'copied').length;
  const failed = result.results.filter((row) => row.error || row.program?.status === 'conflict').length;
  app.toast(`${num(copied)} sınıfın programı kopyalandı${failed ? ` · ${num(failed)} sınıf atlandı` : ''}.`,
    failed ? 'warn' : 'good');
  const panel = openDrawer(app, { title: 'Dönem geçişi kopyası',
    subtitle: `${latest.fromYear} → ${latest.toYear}` });
  const table = dataTable({
    columns: [
      { key: 'pair', label: 'Sınıf', width: 'minmax(0, 1.4fr)',
        cell: (row) => `${names.get(row.from) || row.from} → ${names.get(row.to) || row.to}` },
      { key: 'program', label: 'Program', width: '170px', cell: (row) => {
        if (row.error) return badge('Hata', 'bad');
        const [text, tone] = COPY_STATUS[row.program?.status] || ['—', 'dim'];
        return badge(text, tone);
      } },
      { key: 'curricula', label: 'Müfredat', width: '120px',
        cell: (row) => (row.curricula === null ? '—' : `${num(row.curricula)} program`) },
      { key: 'detail', label: 'Ayrıntı', width: 'minmax(0, 1fr)', className: 'ak-tt-wrap', cell: (row) => {
        if (row.error) return row.error;
        const conflicts = row.program?.conflicts || [];
        if (!conflicts.length) return '—';
        const cell = h('div', 'ak-cell-stack');
        for (const item of conflicts) cell.append(h('small', '', conflictText(item, layout)));
        return cell;
      } },
    ],
    rows: result.results,
    rowKey: (row) => `${row.from}-${row.to}`,
    empty: empty('Sonuç yok'),
  });
  panel.body.append(table.node);
  return result;
}
