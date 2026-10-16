// BBD Akademi — Ders programı › Program şablonları (KM-3; Akademi F-Y2, yalnız KM yüzeyi).
//
// Şablon = gün × zil dilimi × ders (+ isteğe bağlı öğretmen, derslik) satırları. Sınıflara
// uygulanınca (`admin_applyTimetableTemplate`) her hedef sınıfın programı bu satırlarla yazılır;
// hedef başına bağımsızdır (çakışan sınıfa yazılmaz, diğerlerine yazılır — "Haftayı kopyala" ile
// aynı sonuç biçimi). "Çakışmaları denetle" aynı isteği `dryRun: true` ile gönderir. Öğrenci
// çakışması (aynı öğrenci aynı saatte iki sınıfta) yalnız gerekçeyle geçilir. Kaydedilen program
// zil planını Akademi'de yeniden üretir.
//
// Şablon değişikliği uygulanmış programları DEĞİŞTİRMEZ (kopyadır). Yeni şablon boş başlar ya da
// bir sınıfın o gün geçerli programından alınır (`admin_getTimetable`).
//
// Çoklu sınıf ve müfredat (README §14 madde 72): satır isteğe bağlı `classGroupIds` (derse ORTAK
// katılan sınıflar, en çok 12; hedef sınıf her zaman dahildir) ve `programId` taşır; yanıtta adlarıyla
// `classGroups` / `program` gelir. Uygulamada her hedef sınıfta satırın sınıfları = hedef ∪ classGroupIds.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, card } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { AkademiError } from '../api.js';
import {
  DAY_LABEL, actionButton, confirmAction, empty, errorBox, fmtDate, fmtDateTime, formDialog, kv, loader, multiChips,
  num, openDrawer, problem, selectBox, staticList, todayIst, toolbar,
} from '../common.js';
import { fetchPrograms, fillProgramSelect } from '../shared/curriculum-picker.js';
import { copyResultsTable } from './timetable-copy.js';
import { DAY_KEYS, slotLayout, slotName, slotTime, subjectSelect } from './timetable-shared.js';

const LIST = '/v1/admin/timetable-templates';
/** Sözleşme `TimetableTemplateEntry.classGroupIds.maxItems`. */
export const MAX_SHARED_CLASSES = 12;

/** Şablon satırları denetimi: zorunlu alanlar ve aynı hücreye iki ders. */
export function templateProblem(entries) {
  if (!entries.length) return 'En az bir ders satırı ekleyin.';
  const seen = new Set();
  for (const [index, row] of entries.entries()) {
    if (!row.dayOfWeek || !row.bellSlotId || !row.subjectId) return `${index + 1}. satırda gün, ders saati ve ders zorunlu.`;
    const key = `${row.dayOfWeek}|${row.bellSlotId}`;
    if (seen.has(key)) return `${index + 1}. satır başka bir satırla aynı gün ve ders saatinde.`;
    seen.add(key);
    if ((row.classGroupIds || []).length > MAX_SHARED_CLASSES) {
      return `${index + 1}. satırda en çok ${MAX_SHARED_CLASSES} ortak sınıf seçilebilir.`;
    }
  }
  return null;
}

/** Ekrandaki satır → `TimetableTemplateEntry` (yanıt alanları `classGroups` / `program` gönderilmez). */
export function templateEntries(rows) {
  return rows.map((row) => ({ dayOfWeek: row.dayOfWeek, bellSlotId: row.bellSlotId, subjectId: row.subjectId,
    teacherId: row.teacherId || null, room: String(row.room || '').trim() || null,
    classGroupIds: [...new Set(row.classGroupIds || [])], programId: row.programId || null }));
}

/** Yanıttaki şablon satırı → ekran satırı (ortak sınıflar ve müfredat adlarıyla). */
export function rowFromTemplateEntry(entry) {
  const refs = entry.classGroups || [];
  return { dayOfWeek: entry.dayOfWeek, bellSlotId: entry.bellSlotId, subjectId: entry.subjectId || '',
    teacherId: entry.teacherId || '', room: entry.room || '',
    classGroupIds: entry.classGroupIds || refs.map((item) => item.id),
    classRefs: refs, programId: entry.programId || entry.program?.id || '', program: entry.program || null };
}

/**
 * Sınıfın programı (`Timetable.entries`) → şablon satırları (öğretmen ve derslik dahil). README §14 madde
 * 72: `classGroupIds` = satırın `classGroups`'u eksi kaynak sınıf, `programId` = `program.id`.
 */
export function entriesFromTimetable(timetable, sourceClassGroupId = null) {
  return (timetable?.entries || []).filter((entry) => entry.status !== 'orphaned')
    .map((entry) => {
      const refs = (entry.classGroups || []).filter((item) => item?.id && item.id !== sourceClassGroupId);
      return { dayOfWeek: entry.dayOfWeek, bellSlotId: entry.bellSlotId,
        subjectId: entry.subject?.id || entry.subjectId || '', teacherId: entry.teacher?.id || entry.teacherId || '',
        room: entry.room || '', classGroupIds: refs.map((item) => item.id), classRefs: refs,
        programId: entry.program?.id || entry.programId || '', program: entry.program || null };
    });
}

export function templatesTab(app) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_listTimetableTemplates')) {
    node.append(alertBox('Program şablonları için yetkiniz yok (yalnız Kontrol Merkezi’nde, akademik yönetim izniyle).',
      'info'));
    return { node, reload() {}, destroy() {} };
  }
  node.append(toolbar(
    h('span', 'ak-muted', 'Şablon sınıflara uygulanınca programları bu satırlarla yazılır; şablonu sonradan değiştirmek '
      + 'uygulanmış programları değiştirmez.'),
    h('span', 'kit-spacer'),
    actionButton(app, 'admin_createTimetableTemplate', 'Yeni şablon', { variant: 'primary', onClick: () => create() }),
  ));
  const list = staticList({
    load: () => app.api.get(LIST),
    rowKey: (row) => row.id,
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.name), row.description ? h('small', '', row.description) : '');
        return cell;
      } },
      { key: 'hours', label: 'Ders saati', align: 'num', cell: (row) => num((row.entries || []).length) },
      { key: 'used', label: 'Kullanan sınıf', align: 'num', cell: (row) => num(row.usedByGroupCount || 0) },
      { key: 'updated', label: 'Son değişiklik', cell: (row) => fmtDateTime(row.updatedAt) },
    ],
    onRow: (row) => openTemplate(app, row.id, { onChange: () => list.reload() }),
    empty: { title: 'Şablon yok', text: '“Yeni şablon” ile boş başlayın ya da bir sınıfın programından alın.' },
  });
  node.append(list.node);

  async function create() {
    const saved = await editTemplate(app, null);
    if (!saved) return;
    app.toast('Şablon oluşturuldu.', 'good');
    list.reload();
    openTemplate(app, saved.id, { onChange: () => list.reload() });
  }

  return { node, reload: () => list.reload(), destroy() {} };
}

async function lookups(app) {
  const [bells, subjects, teachers, classes] = await Promise.all([
    app.lookups.bellSlots(),
    app.lookups.subjects().catch(() => []),
    app.lookups.teachers().catch(() => []),
    app.lookups.classGroups().catch(() => []),
  ]);
  return { layout: slotLayout(bells?.slots || []), subjects, teachers, classes };
}

/** Şablon çekmecesi: satırlar + düzenle / sil / uygula. */
export function openTemplate(app, id, { onChange } = {}) {
  const panel = openDrawer(app, { title: 'Program şablonu', subtitle: 'Yükleniyor…' });
  const subtitle = panel.node.querySelector('.kit-drawer-sub');
  let refs = null;
  const view = loader(async () => {
    const [template, base] = await Promise.all([app.api.get(`/v1/admin/timetable-templates/${id}`), lookups(app)]);
    return { template, ...base };
  }, (data) => {
    refs = data;
    const { template, layout, subjects, teachers } = data;
    panel.setTitle(template.name);
    if (subtitle) subtitle.textContent = `${num((template.entries || []).length)} ders saati · `
      + `${num(template.usedByGroupCount || 0)} sınıfta kullanıldı`;
    const subjectName = new Map(subjects.map((item) => [item.id, item.name]));
    const teacherName = new Map(teachers.map((item) => [item.id, item.displayName]));
    const rows = [...(template.entries || [])].sort((a, b) => (DAY_KEYS.indexOf(a.dayOfWeek) - DAY_KEYS.indexOf(b.dayOfWeek))
      || String(layout.slot(a.dayOfWeek, a.bellSlotId)?.startTime || '').localeCompare(String(layout.slot(b.dayOfWeek, b.bellSlotId)?.startTime || '')));
    const table = dataTable({ dense: true, rows, rowKey: (row) => `${row.dayOfWeek}|${row.bellSlotId}`, columns: [
      { key: 'when', label: 'Gün ve saat', width: 'minmax(0, 1.6fr)', cell: (row) => slotName(layout, row.dayOfWeek, row.bellSlotId) },
      { key: 'subject', label: 'Ders', cell: (row) => subjectName.get(row.subjectId) || row.subjectId },
      { key: 'teacher', label: 'Öğretmen', cell: (row) => (row.teacherId ? teacherName.get(row.teacherId) || 'Pasif öğretmen' : '—') },
      { key: 'room', label: 'Derslik', cell: (row) => row.room || '—' },
      { key: 'classes', label: 'Ortak sınıflar', cell: (row) => (row.classGroups || []).map((item) => item.name).join(', ')
        || '—' },
      { key: 'program', label: 'Müfredat', cell: (row) => row.program?.title || '—' },
    ], empty: empty('Satır yok') });
    return [
      toolbar(
        actionButton(app, 'admin_applyTimetableTemplate', 'Sınıflara uygula', { variant: 'primary', onClick: () => apply() }),
        actionButton(app, 'admin_updateTimetableTemplate', 'Düzenle', { onClick: () => edit() }),
        h('span', 'kit-spacer'),
        actionButton(app, 'admin_deleteTimetableTemplate', 'Sil', { variant: 'ghost', onClick: () => remove() }),
      ),
      template.description ? h('p', 'ak-muted', template.description) : null,
      kv([['Son değişiklik', fmtDateTime(template.updatedAt)], ['Kullanan sınıf', num(template.usedByGroupCount || 0)]]),
      table.node,
    ].filter(Boolean);
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();

  async function edit() {
    const saved = await editTemplate(app, refs.template, refs);
    if (!saved) return;
    app.toast('Şablon kaydedildi. Uygulanmış programlar değişmedi.', 'good');
    view.reload();
    onChange?.();
  }

  async function remove() {
    const ok = await confirmAction(app, { title: 'Şablonu sil', danger: true, confirmLabel: 'Sil',
      description: `“${refs.template.name}” silinir. Bu şablonla yazılmış sınıf programları değişmez.` });
    if (!ok) return;
    try {
      await app.api.del(`${LIST}/${id}`);
      app.toast('Şablon silindi.', 'good');
      panel.close();
      onChange?.();
    } catch (failure) {
      app.toast(errorText(failure), 'bad');
    }
  }

  async function apply() {
    const result = await applyDialog(app, refs.template, refs);
    if (!result) return;
    view.reload();
    onChange?.();
  }

  return panel;
}

function errorText(failure) {
  return problem(failure);
}

/** Şablon oluştur / düzenle. `current` null → yeni (boş ya da bir sınıfın programından). */
export async function editTemplate(app, current, loaded = null) {
  const { layout, subjects, teachers, classes } = loaded || await lookups(app);
  const days = layout.days.length ? layout.days : DAY_KEYS.slice(0, 5);
  const rows = (current?.entries || []).map(rowFromTemplateEntry);
  const classNames = new Map(classes.map((item) => [item.id, item.name]));
  const listNode = h('div', 'ak-stack');

  const teacherOpts = [{ value: '', label: 'Öğretmen yok' },
    ...teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))];

  function slotOptions(day) {
    return [{ value: '', label: 'Ders saati' }, ...layout.lessonsOf(day).map(({ row, slot }) => ({ value: slot.id,
      label: `${row.label} (${slotTime(slot)})` }))];
  }

  function paint() {
    listNode.replaceChildren();
    if (!rows.length) listNode.append(h('span', 'ak-muted', 'Henüz satır yok.'));
    rows.forEach((row, index) => {
      const line = h('div', 'ak-tt-tpl-row');
      const day = selectBox(days.map((key) => ({ value: key, label: DAY_LABEL[key] || key })), row.dayOfWeek || days[0],
        (value) => { row.dayOfWeek = value; row.bellSlotId = ''; paint(); }, { label: `${index + 1}. satır gün` });
      if (!row.dayOfWeek) row.dayOfWeek = days[0];
      const slot = selectBox(slotOptions(row.dayOfWeek), row.bellSlotId || '', (value) => { row.bellSlotId = value; },
        { label: `${index + 1}. satır ders saati` });
      const subject = subjectSelect(subjects, row.subjectId || '', { onChange: (value) => {
        // Müfredat derse bağlıdır: ders değişince eski bağlantı düşer.
        if (value !== row.subjectId) { row.programId = ''; row.program = null; }
        row.subjectId = value;
        paint();
      }, label: `${index + 1}. satır ders` });
      const teacher = selectBox(teacherOpts, row.teacherId || '', (value) => { row.teacherId = value; },
        { label: `${index + 1}. satır öğretmen` });
      const room = h('input', 'kit-input');
      room.type = 'text';
      room.maxLength = 40;
      room.placeholder = 'Derslik';
      room.value = row.room || '';
      room.setAttribute('aria-label', `${index + 1}. satır derslik`);
      room.addEventListener('input', () => { row.room = room.value; });
      const drop = button('Kaldır', { variant: 'ghost', onClick: () => { rows.splice(index, 1); paint(); } });
      line.append(day.node, slot.node, subject, teacher.node, room, drop);
      listNode.append(line, extraLine(row, index));
    });
  }

  /** Satırın ikinci çizgisi: ortak sınıflar (çip + seç) ve isteğe bağlı müfredat. */
  function extraLine(row, index) {
    const line = h('div', 'ak-tt-tpl-extra ak-inline');
    const chips = h('div', 'ak-chips');
    const chosen = row.classGroupIds || [];
    const refName = new Map((row.classRefs || []).map((item) => [item.id, item.name]));
    for (const id of chosen) {
      const name = classNames.get(id) || refName.get(id) || 'Sınıf';
      const chip = h('span', 'kit-chip on ak-tt-classchip');
      const remove = h('button', 'ak-tt-classchip-x', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `${index + 1}. satır: ${name} sınıfını çıkar`);
      remove.addEventListener('click', () => { row.classGroupIds = chosen.filter((item) => item !== id); paint(); });
      chip.append(h('span', '', name), remove);
      chips.append(chip);
    }
    const full = chosen.length >= MAX_SHARED_CLASSES;
    const pick = selectBox([{ value: '', label: full ? `En çok ${MAX_SHARED_CLASSES} sınıf` : '+ Ortak sınıf (birlikte ders)' },
      ...classes.filter((item) => !chosen.includes(item.id)).map((item) => ({ value: item.id, label: item.name }))], '',
    (value) => {
      if (!value) return;
      row.classGroupIds = [...chosen, value];
      paint();
    }, { label: `${index + 1}. satır ortak sınıf ekle` });
    pick.node.disabled = full;
    const program = h('select', 'kit-select');
    program.setAttribute('aria-label', `${index + 1}. satır müfredat (isteğe bağlı)`);
    loadPrograms(row, program);
    program.addEventListener('change', () => { row.programId = program.value; });
    line.append(h('span', 'ak-muted', 'Uygulanan sınıf hep dahil.'), chips, pick.node, program);
    return line;
  }

  /** Müfredat seçenekleri: satırın dersinin programları (önbellekli); ders yoksa kapalı. */
  async function loadPrograms(row, select) {
    const keep = row.programId || '';
    if (!row.subjectId) {
      fillProgramSelect(select, [], { placeholder: 'Müfredat: önce ders seçin' });
      select.disabled = true;
      return;
    }
    fillProgramSelect(select, row.program ? [row.program] : [], { placeholder: 'Müfredat yok' });
    select.value = keep;
    try {
      const { items } = await fetchPrograms(app, { subjectId: row.subjectId });
      const list = [...items];
      if (row.program && !list.some((item) => item.id === row.program.id)) list.push(row.program);
      fillProgramSelect(select, list, { placeholder: 'Müfredat yok', recommendedFirst: false });
      select.value = row.programId || '';
    } catch {
      // Liste alınamazsa mevcut seçim korunur.
    }
  }

  const fromClass = !current && classes.length ? selectBox([{ value: '', label: 'Bir sınıfın programından al…' },
    ...classes.map((item) => ({ value: item.id, label: item.name }))], '', async (value) => {
    if (!value) return;
    try {
      const timetable = await app.api.get(`/v1/admin/class-groups/${value}/timetable`, { date: todayIst() });
      rows.splice(0, rows.length, ...entriesFromTimetable(timetable, value));
      paint();
      app.toast(`${num(rows.length)} ders saati alındı; düzenleyip kaydedin.`, 'good');
    } catch (failure) {
      app.toast(errorText(failure), 'bad');
    }
  }, { label: 'Sınıfın programından al' }) : null;

  return formDialog(app, {
    title: current ? `${current.name}: düzenle` : 'Yeni program şablonu',
    description: current ? 'Şablon değişikliği uygulanmış programları değiştirmez.' : '',
    wide: true,
    value: { name: current?.name || '', description: current?.description || '' },
    fields: [
      { key: 'name', label: 'Ad', required: true, maxLength: 80 },
      { key: 'description', label: 'Açıklama (isteğe bağlı)', maxLength: 300 },
      { key: 'entries', label: 'Ders saatleri', type: 'custom', wide: true, render: () => {
        const wrap = h('div', 'ak-stack');
        const add = button('+ Satır ekle', { variant: 'ghost', onClick: () => {
          rows.push({ dayOfWeek: rows.at(-1)?.dayOfWeek || days[0], bellSlotId: '', subjectId: '', teacherId: '', room: '',
            classGroupIds: [], classRefs: [], programId: '', program: null });
          paint();
        } });
        if (!layout.rows.length) wrap.append(alertBox('Zil dilimi tanımlı değil; önce Zil dilimleri sekmesinden ders saatlerini kurun.', 'warn'));
        wrap.append(toolbar(fromClass?.node, h('span', 'kit-spacer'), add), listNode);
        paint();
        return wrap;
      }, validate: () => templateProblem(rows) },
    ],
    submitLabel: current ? 'Kaydet' : 'Şablonu oluştur',
    onSubmit: (draft, { key }) => {
      const body = { name: String(draft.name || '').trim(), description: String(draft.description || '').trim() || null,
        entries: templateEntries(rows) };
      return current ? app.api.put(`${LIST}/${current.id}`, body) : app.api.post(LIST, body, { key });
    },
  });
}

/** Sınıflara uygula: ön denetim (`dryRun`) + gerçek uygulama; sonuç tablosu çekmecede. */
export async function applyDialog(app, template, { classes, layout }) {
  const today = todayIst();
  const options = classes.map((item) => ({ value: item.id, label: item.name }));
  const checkSlot = h('div', 'ak-stack');
  const body = (draft, dryRun) => ({
    classGroupIds: draft.classGroupIds || [], validFrom: draft.validFrom,
    validTo: draft.openEnded ? null : (draft.validTo || null), keepTeachers: Boolean(draft.keepTeachers),
    allowStudentConflicts: Boolean(draft.allowStudentConflicts),
    overrideReason: draft.allowStudentConflicts ? String(draft.overrideReason || '').trim() || null : null, dryRun,
  });
  const problemOf = (draft) => {
    if (!(draft.classGroupIds || []).length) return 'En az bir sınıf seçin.';
    if (!draft.validFrom) return 'Geçerlilik başlangıcını seçin.';
    if (!draft.openEnded && draft.validTo && draft.validTo < draft.validFrom) return 'Bitiş başlangıçtan önce olamaz.';
    if (draft.allowStudentConflicts && String(draft.overrideReason || '').trim().length < 3) {
      return 'Öğrenci çakışmasına izin için gerekçe yazın.';
    }
    return null;
  };
  const result = await formDialog(app, {
    title: `“${template.name}” şablonunu uygula`,
    description: 'Her sınıf ayrı işlenir: çakışan sınıfa yazılmaz, diğerlerine yazılır. Seçilen aralıkta sınıfın programı '
      + 'bununla değişir ve zil planı yeniden üretilir. Satırların ortak sınıfları ve müfredatı da programa taşınır; '
      + 'ortak sınıfların öğrencileri öğrenci çakışması denetimine girer.',
    wide: true,
    value: { classGroupIds: [], validFrom: today, validTo: '', openEnded: true, keepTeachers: true,
      allowStudentConflicts: false, overrideReason: '' },
    fields: [
      { key: 'classGroupIds', label: 'Sınıflar', type: 'custom', required: true,
        render: (draft, set) => multiChips(options, [], set).node },
      { key: 'validFrom', label: 'Geçerlilik başlangıcı', type: 'date', required: true },
      { key: 'validTo', label: 'Geçerlilik bitişi', type: 'date', hint: '“Süresiz” işaretliyse dikkate alınmaz.' },
      { key: 'openEnded', label: 'Süresiz (bitiş tarihi yok)', type: 'checkbox' },
      { key: 'keepTeachers', label: 'Şablondaki öğretmenler de yazılsın', type: 'checkbox' },
      { key: 'allowStudentConflicts', label: 'Öğrenci çakışmasına izin ver (gerekçeli)', type: 'checkbox' },
      { key: 'overrideReason', label: 'Gerekçe (öğrenci çakışmasına izin verildiyse)', maxLength: 300 },
      { key: 'check', label: 'Ön denetim', type: 'custom', wide: true, render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Çakışmaları denetle', { onClick: async () => {
          const issue = problemOf(draft);
          if (issue) { checkSlot.replaceChildren(alertBox(issue, 'info')); return; }
          go.disabled = true;
          try {
            const check = await app.api.post(`${LIST}/${template.id}/apply`, body(draft, true));
            checkSlot.replaceChildren(copyResultsTable(check, { classGroups: classes, layout }));
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
    submitLabel: 'Uygula',
    onSubmit: (draft, { key }) => {
      const issue = problemOf(draft);
      if (issue) throw new AkademiError({ code: 'validation_error', message: issue });
      return app.api.post(`${LIST}/${template.id}/apply`, body(draft, false), { key });
    },
  });
  if (!result || result === true) return null;
  const results = result.results || [];
  const copied = results.filter((item) => item.status === 'copied').length;
  const skipped = results.filter((item) => item.status === 'conflict').length;
  app.toast(`${num(copied)} sınıfa uygulandı${skipped ? ` · ${num(skipped)} sınıf çakışma nedeniyle atlandı` : ''}.`,
    skipped ? 'warn' : 'good');
  const panel = openDrawer(app, { title: 'Şablon uygulama sonucu', subtitle: `${template.name} · ${fmtDate(today)}` });
  panel.body.append(card('', copyResultsTable(result, { classGroups: classes, layout })));
  if (copied) app.api.invalidate?.('/v1/admin/class-groups');
  return result;
}

