// BBD Akademi — Ders programı › Etüt ve tek seferlik dersler (F-A6).
//
// Liste imleçlidir (`admin_listLessonEvents`, tarih aralığı + tür + öğretmen +
// öğrenci süzgeci); satır başına ek istek atılmaz, katılımcılar liste yükünde
// gelir. Ayrıntı çekmecesi tek kaydı okur.
//
// ZAMAN: gün + zil dilimi ya da elle saat aralığı. Zil dilimi seçilince saatler
// dilimden dolar; Akademi'ye her zaman `startsAt/endsAt` (Europe/Istanbul,
// +03:00) gider. Öğretmen/derslik çakışması Akademi'de denetlenir
// (`409 timetable_conflict`) ve pencerede okunur cümleyle gösterilir.
//
// DEĞİŞTİRİLEMEYENLER: sözleşmede `PATCH` ders, tür ve sınıf grubu almaz;
// bunlar düzenleme penceresinde salt okunur yazılır. İptal `status: cancelled`
// ile yapılır, gerekçe nota yazılır. Silme yalnız yoklaması alınmamış kayıtta
// mümkündür (`409 has_attendance` → iptal önerilir).
//
// Etüt TALEPLERİ kuyruğu Talepler bölümündedir (`app.go('requests', {tab: 'etut'})`).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { AkademiError } from './api.js';
import {
  TZ, actionButton, clockInput, confirmAction, dateInput, empty, fmtDate, fmtDateTime,
  fmtTime, formDialog, istanbulStamp, kv, loader, multiChips, num, openDrawer, pagedList,
  personName, problem, reasonDialog, runAction, selectBox, statusBadge, studentPicker, todayIst,
  toolbar,
} from './common.js';
import {
  CLOCK, SHORT_DAY, dayKeyOf, readableConflict, slotLayout, slotTime, teacherOptions,
} from './timetable-shared.js';

export const EVENT_KINDS = { etut: ['Etüt', 'info'], one_off: ['Tek seferlik ders', ''] };
export const EVENT_STATUS = { scheduled: ['Planlandı', 'info'], cancelled: ['İptal', 'dim'],
  done: ['Yapıldı', 'good'] };

/** Gelecek odaklı hazır aralıklar (kitin `dateRange` kuralı: başlangıç = bugün − (gün−1) − ofset). */
const RANGE_PRESETS = [
  { key: 'today', label: 'Bugün', days: 1 },
  { key: 'next7', label: 'Önümüzdeki 7 gün', days: 7, offset: -6 },
  { key: 'next30', label: 'Önümüzdeki 30 gün', days: 30, offset: -29 },
  { key: 'last30', label: 'Son 30 gün', days: 30 },
];

export function eventTitle(event) {
  return event?.title || event?.subject?.name || (event?.kind === 'etut' ? 'Etüt' : 'Ders');
}

/** Zaman damgası → Europe/Istanbul {day: 'YYYY-MM-DD', clock: 'HH:MM'}. */
function localParts(stamp) {
  if (!stamp) return { day: todayIst(), clock: '' };
  const date = new Date(String(stamp));
  if (Number.isNaN(date.getTime())) return { day: todayIst(), clock: '' };
  return { day: date.toLocaleDateString('sv-SE', { timeZone: TZ }), clock: fmtTime(stamp) };
}

function participantsText(event) {
  const groups = (event.classGroups || []).map((item) => item.name).filter(Boolean);
  const students = event.students || [];
  const parts = [];
  if (groups.length) parts.push(groups.join(', '));
  if (students.length && students.length <= 2 && !groups.length) {
    parts.push(students.map((item) => item.displayName).join(', '));
  } else if (students.length) {
    parts.push(`${num(students.length)} öğrenci`);
  }
  return parts.join(' · ') || '—';
}

// ------------------------------------------------------------------- liste

export function eventsTab(app) {
  const node = h('div', 'ak-stack');
  const filters = { from: todayIst(), to: todayIst(13), kind: '', teacherId: '', studentId: '' };

  const range = dateRange({ start: filters.from, end: filters.to, label: 'Tarih', presets: RANGE_PRESETS,
    onChange: (value) => {
      const from = value.start || filters.from;
      const to = value.end || filters.to;
      if (from === filters.from && to === filters.to) return;
      filters.from = from;
      filters.to = to < from ? from : to;
      list.reload();
    } });
  const kindSelect = selectBox([{ value: '', label: 'Tüm türler' }, { value: 'etut', label: 'Etütler' },
    { value: 'one_off', label: 'Tek seferlik dersler' }], '', (value) => {
    filters.kind = value;
    list.reload();
  }, { label: 'Tür' });
  const teacherSelect = selectBox([{ value: '', label: 'Tüm öğretmenler' }], '', (value) => {
    filters.teacherId = value;
    list.reload();
  }, { label: 'Öğretmen' });
  app.lookups.teachers().then((teachers) => teacherSelect.options(teacherOptions(teachers, null,
    'Tüm öğretmenler'))).catch(() => {});

  const studentSlot = h('div', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => {
    filters.studentId = student.id;
    paintStudent(student);
    list.reload();
  } });
  function paintStudent(student) {
    studentSlot.replaceChildren();
    if (!student) {
      studentSlot.append(picker.node);
      return;
    }
    const chip = h('span', 'ak-tt-person');
    chip.append(h('span', '', student.displayName || 'Öğrenci'));
    const clear = h('button', 'ak-tt-person-x', '×');
    clear.type = 'button';
    clear.setAttribute('aria-label', 'Öğrenci süzgecini kaldır');
    clear.addEventListener('click', () => {
      filters.studentId = '';
      paintStudent(null);
      list.reload();
    });
    chip.append(clear);
    studentSlot.append(chip);
  }
  paintStudent(null);

  const createEtut = actionButton(app, 'admin_createLessonEvent', 'Yeni etüt', { variant: 'primary',
    onClick: () => create('etut') });
  const createOneOff = actionButton(app, 'admin_createLessonEvent', 'Tek seferlik ders', {
    onClick: () => create('one_off') });
  const requestsLink = app.has('requests')
    ? button('Etüt talepleri →', { variant: 'ghost', title: 'Talepler bölümündeki etüt kuyruğu',
      onClick: () => app.go('requests', { tab: 'etut' }) })
    : null;

  node.append(toolbar(range.node), toolbar(kindSelect.node, teacherSelect.node, studentSlot,
    h('span', 'kit-spacer'), requestsLink, createOneOff, createEtut));

  const list = pagedList({
    app,
    path: '/v1/admin/lesson-events',
    query: () => ({ from: filters.from, to: filters.to, kind: filters.kind || undefined,
      teacherId: filters.teacherId || undefined, studentId: filters.studentId || undefined }),
    limit: 50,
    columns: [
      { key: 'when', label: 'Zaman', width: '150px', cell: (row) => {
        const start = localParts(row.startsAt);
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', `${fmtDate(start.day)} ${SHORT_DAY[dayKeyOf(start.day)] || ''}`),
          h('small', '', `${fmtTime(row.startsAt)}–${fmtTime(row.endsAt)}`));
        return cell;
      } },
      { key: 'kind', label: 'Tür', width: '130px', cell: (row) => statusBadge(EVENT_KINDS, row.kind) },
      { key: 'subject', label: 'Ders / konu', width: 'minmax(0, 1.5fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', eventTitle(row)),
          h('small', '', [row.title ? row.subject?.name : null, row.topic?.name].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1fr)', cell: (row) => personName(row.teacher) },
      { key: 'who', label: 'Katılımcılar', width: 'minmax(0, 1.3fr)', cell: (row) => participantsText(row) },
      { key: 'room', label: 'Derslik', width: '90px', cell: (row) => row.room || '—' },
      { key: 'status', label: 'Durum', width: '110px', cell: (row) => statusBadge(EVENT_STATUS, row.status) },
    ],
    onRow: (row) => open(row.id),
    empty: { title: 'Bu aralıkta etüt ya da tek seferlik ders yok',
      text: 'Tarih aralığını genişletin ya da süzgeçleri temizleyin.' },
  });
  node.append(list.node);

  function open(id) {
    openLessonEvent(app, id, {
      onChange: (next) => list.replace((item) => item.id === next.id, next),
      onRemove: (removed) => list.remove((item) => item.id === removed),
    });
  }

  async function create(kind) {
    const created = await lessonEventDialog(app, { kind });
    if (!created) return;
    app.toast(`${EVENT_KINDS[created.kind]?.[0] || 'Kayıt'} oluşturuldu: ${eventTitle(created)} · `
      + `${fmtDateTime(created.startsAt)}`, 'good');
    list.reload();
    open(created.id);
  }

  return {
    node,
    reload: () => list.reload(),
    open,
    destroy() {
      range.destroy();
      picker.destroy();
      list.destroy();
    },
  };
}

// --------------------------------------------------------------- ayrıntı

/**
 * Etüt / tek seferlik ders çekmecesi. Derin bağlantı (`{lessonEventId}`) ve
 * çakışma kutusundaki "Etüdü aç" da bunu kullanır.
 */
export function openLessonEvent(app, id, { onChange, onRemove } = {}) {
  const panel = openDrawer(app, { title: 'Etüt / ders', subtitle: 'Yükleniyor…' });
  const notice = h('div', 'ak-stack');
  let current = null;
  const view = loader(() => app.api.get(`/v1/admin/lesson-events/${id}`), (event) => {
    current = event;
    panel.setTitle(`${eventTitle(event)} — ${EVENT_KINDS[event.kind]?.[0] || 'Ders'}`);
    return render(event);
  }, { rows: 6, columns: 3 });
  panel.body.append(notice, view.node);
  view.reload();

  function render(event) {
    const nodes = [];
    if (event.status === 'cancelled') {
      nodes.push(alertBox(`Bu ${event.kind === 'etut' ? 'etüt' : 'ders'} iptal edildi.${event.note ? ` ${event.note}` : ''}`, 'warn'));
    }
    const start = localParts(event.startsAt);
    nodes.push(card('', kv([
      ['Tür', statusBadge(EVENT_KINDS, event.kind)],
      ['Zaman', `${fmtDate(start.day)} ${SHORT_DAY[dayKeyOf(start.day)] || ''} · `
        + `${fmtTime(event.startsAt)}–${fmtTime(event.endsAt)}`],
      ['Ders', event.subject?.name || '—'],
      ['Konu', event.topic?.name || '—'],
      ['Başlık', event.title || '—'],
      ['Öğretmen', personName(event.teacher)],
      ['Derslik', event.room || '—'],
      ['Sınıflar', (event.classGroups || []).map((item) => item.name).join(', ') || '—'],
      ['Etüt talepleri', (event.etutRequestIds || []).length
        ? `${num(event.etutRequestIds.length)} talep bu kayda bağlı` : '—'],
      ['Not', event.note || '—'],
      ['Durum', statusBadge(EVENT_STATUS, event.status)],
    ])));

    const tools = h('div', 'ak-inline');
    if (event.status !== 'cancelled') {
      tools.append(actionButton(app, 'admin_updateLessonEvent', 'Düzenle', { variant: 'primary',
        onClick: () => edit(event) }));
    }
    if (event.status === 'scheduled') {
      tools.append(actionButton(app, 'admin_updateLessonEvent', 'İptal et', { onClick: () => cancel(event) }));
    }
    tools.append(button('Yoklamayı aç', { title: 'Yoklama bölümünde bu dersin listesi',
      onClick: () => {
        panel.close();
        app.go('attendance', { attendanceSessionId: `ev:${event.id}` });
      } }));
    tools.append(h('span', 'kit-spacer'), actionButton(app, 'admin_deleteLessonEvent', 'Sil', {
      variant: 'danger', onClick: () => remove(event) }));
    nodes.push(tools);

    const students = dataTable({
      columns: [
        { key: 'name', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', row.displayName), h('small', '', row.externalRef || ''));
          return cell;
        } },
        { key: 'class', label: 'Sınıf', cell: (row) => row.classGroup?.name || '—' },
        { key: 'status', label: '', width: '130px', cell: (row) => (row.status === 'missing'
          ? badge('KM dizininde yok', 'warn') : '') },
      ],
      rows: event.students || [],
      dense: true,
      empty: empty('Öğrenci yok', (event.classGroups || []).length
        ? 'Katılımcılar sınıf üzerinden belirleniyor.' : 'Bu kayda henüz öğrenci eklenmemiş.'),
    });
    nodes.push(card(`Katılımcılar (${num((event.students || []).length)})`, students.node));
    return nodes;
  }

  async function edit(event) {
    const next = await lessonEventDialog(app, { event });
    if (!next) return;
    app.toast('Değişiklikler kaydedildi.', 'good');
    onChange?.(next);
    view.reload();
  }

  async function cancel(event) {
    const reason = await reasonDialog(app, {
      title: `${eventTitle(event)}: iptal et`,
      description: `${fmtDateTime(event.startsAt)}. Katılımcılara ve velilere iptal bildirimi gider; `
        + 'gerekçe kaydın notuna yazılır.',
      confirmLabel: 'İptal et',
      presets: ['Öğretmen izinli', 'Yeterli katılım yok', 'Derslik kullanılamıyor', 'Tarih değişti'],
    });
    if (!reason) return;
    const note = [`İptal gerekçesi: ${reason}`, event.note].filter(Boolean).join(' · ').slice(0, 300);
    const next = await runAction(app, () => app.api.patch(`/v1/admin/lesson-events/${event.id}`,
      { status: 'cancelled', note, notify: true }), { success: 'İptal edildi; katılımcılara bildirildi.' });
    if (next) {
      onChange?.(next);
      view.reload();
    }
  }

  async function remove(event) {
    const ok = await confirmAction(app, {
      title: `${eventTitle(event)}: sil`,
      description: 'Kayıt kalıcı olarak silinir. Yoklaması alınmış bir ders silinemez; onun yerine iptal edin.',
      confirmLabel: 'Sil',
      danger: true,
    });
    if (!ok) return;
    notice.replaceChildren();
    try {
      await app.api.del(`/v1/admin/lesson-events/${event.id}`);
      app.toast('Silindi.', 'good');
      onRemove?.(event.id);
      panel.close();
    } catch (failure) {
      if (failure?.code === 'has_attendance') {
        const box = h('div', 'ak-tt-notice warn');
        box.append(h('b', '', 'Bu kaydın yoklaması alınmış; silinemez.'),
          h('span', '', `${problem(failure)} Devamsızlık kaydı korunur; dersi iptal edebilirsiniz.`));
        if (current?.status === 'scheduled') {
          const tools = h('div', 'ak-inline');
          tools.append(actionButton(app, 'admin_updateLessonEvent', 'İptal et', {
            onClick: () => cancel(current) }));
          box.append(tools);
        }
        notice.replaceChildren(box);
      } else {
        app.toast(problem(failure, 'Silinemedi.'), 'bad');
      }
    }
  }

  return panel;
}

// --------------------------------------------------------------- pencere

/**
 * Oluştur (`kind`) ya da düzenle (`event`). Sonuç: Akademi'nin döndürdüğü
 * kayıt ya da vazgeçildiyse null.
 */
export async function lessonEventDialog(app, { kind = 'etut', event = null } = {}) {
  const editing = Boolean(event);
  const [subjects, teachers, bells, classGroups] = await Promise.all([
    editing ? Promise.resolve([]) : app.lookups.subjects().catch(() => []),
    app.lookups.teachers().catch(() => []),
    app.lookups.bellSlots().catch(() => null),
    editing ? Promise.resolve([]) : app.lookups.classGroups().catch(() => []),
  ]);
  const layout = slotLayout(bells?.slots || []);
  const disposers = [];
  const start = localParts(event?.startsAt);
  const end = localParts(event?.endsAt);
  // Özel alanların ilk değeri `value`a KONMAZ: formDialog, formGrid'i bütün
  // `value` ile kurar ve her ızgara değişikliğinde taslağı onunla ezer — özel
  // alanın yazdığı değer eski hâline dönerdi. Özel alanlar kendi ilk değerini
  // buradan alır ve çizilirken taslağa yazar.
  const value = editing
    ? { title: event.title || '', teacherId: event.teacher?.id || '', room: event.room || '' }
    : { kind, subjectId: '', title: '', teacherId: '', room: '' };
  const initial = {
    time: editing ? { day: start.day, from: start.clock, to: end.clock } : { day: todayIst(), from: '', to: '' },
    note: editing ? event.note || '' : '',
  };

  const subjectOptions = [{ value: '', label: 'Ders seçilmedi' },
    ...[...subjects].sort((a, b) => String(a.level).localeCompare(String(b.level))
      || String(a.name).localeCompare(String(b.name), 'tr'))
      .map((item) => ({ value: item.id, label: item.name }))];

  const fields = [
    editing ? null : { key: 'kind', label: 'Tür', type: 'select', options: [
      { value: 'etut', label: 'Etüt' }, { value: 'one_off', label: 'Tek seferlik ders' }] },
    editing ? null : { key: 'subjectId', label: 'Ders', type: 'select', options: subjectOptions },
    { key: 'title', label: 'Başlık (isteğe bağlı)', maxLength: 80, placeholder: 'ör. Üslü sayılar tekrarı',
      validate: (raw, draft) => (!editing && !String(raw || '').trim() && !draft.subjectId
        ? 'Ders seçin ya da başlık yazın.' : null) },
    { key: 'teacherId', label: 'Öğretmen', type: 'select', options: teacherOptions(teachers, event?.teacher) },
    { key: 'room', label: 'Derslik', maxLength: 40, placeholder: 'ör. 204' },
    { key: 'time', label: 'Zaman', type: 'custom', required: true,
      render: timeField(layout, initial.time, disposers),
      validate: (raw) => {
        if (!raw?.day) return 'Gün seçin.';
        if (!raw.from || !raw.to) return 'Başlangıç ve bitiş saatini yazın ya da zil dilimi seçin.';
        if (!CLOCK.test(raw.from) || !CLOCK.test(raw.to)) return 'Saati 09:30 gibi yazın.';
        return raw.from >= raw.to ? 'Bitiş saati başlangıçtan sonra olmalı.' : null;
      } },
    editing ? null : { key: 'classGroupIds', label: 'Sınıflar', type: 'custom',
      hint: 'Seçilen sınıfların bütün öğrencileri katılır (tek seferlik ders için).',
      render: (draft, set) => {
        set([]);
        return multiChips(classGroups.map((item) => ({ value: item.id, label: item.name })), [], set);
      } },
    { key: 'studentIds', label: 'Öğrenciler', type: 'custom',
      hint: editing && (event.classGroups || []).length
        ? `Sınıflar (${event.classGroups.map((item) => item.name).join(', ')}) değiştirilemez.` : '',
      render: studentsField(app, event?.students || [], disposers),
      validate: (raw, draft) => {
        if ((raw || []).length) return null;
        if (editing) return (event.classGroups || []).length ? null : 'En az bir öğrenci kalmalı.';
        return (draft.classGroupIds || []).length ? null : 'En az bir öğrenci ya da sınıf seçin.';
      } },
    { key: 'note', label: 'Not', type: 'custom', render: (draft, set) => {
      const area = h('textarea', 'kit-textarea');
      area.maxLength = 300;
      area.placeholder = 'Katılımcıların göreceği kısa not (isteğe bağlı)';
      area.value = initial.note;
      set(initial.note);
      area.addEventListener('input', () => set(area.value));
      return area;
    } },
    { key: 'notify', label: 'Bildirim', type: 'custom', render: (draft, set) => {
      const wrap = h('label', 'ak-tt-check');
      const check = h('input', 'kit-check');
      check.type = 'checkbox';
      check.checked = true;
      set(true);
      check.addEventListener('change', () => set(check.checked));
      wrap.append(check, h('span', '', 'Öğrencilere ve velilere bildir'));
      return wrap;
    } },
  ];

  const description = editing
    ? [`${EVENT_KINDS[event.kind]?.[0] || 'Ders'}${event.subject?.name ? ` · ${event.subject.name}` : ''}. `
      + 'Tür, ders ve sınıflar sonradan değiştirilemez; gerekirse iptal edip yenisini açın.',
    (event.etutRequestIds || []).length ? 'Bu kayıt etüt talebine bağlı; talep sahibine de bildirim gider.' : '']
      .filter(Boolean).join('\n')
    : (kind === 'etut'
      ? 'Bir ya da birkaç öğrenci için ders dışı etüt. Öğretmen ve derslik çakışması Akademi\'de denetlenir.'
      : 'Programda olmayan tek seferlik ders (telafi, deneme çözümü…). Sınıf ya da öğrenci seçin.');

  const result = await formDialog(app, {
    title: editing ? `${eventTitle(event)} — düzenle` : (kind === 'etut' ? 'Yeni etüt' : 'Yeni tek seferlik ders'),
    description,
    wide: true,
    value,
    fields,
    submitLabel: editing ? 'Değişiklikleri kaydet' : 'Oluştur',
    onSubmit: async (draft, { key }) => {
      const time = draft.time || {};
      const common = {
        title: String(draft.title || '').trim() || null,
        teacherId: draft.teacherId || null,
        room: String(draft.room || '').trim() || null,
        startsAt: istanbulStamp(time.day, time.from),
        endsAt: istanbulStamp(time.day, time.to),
        studentIds: draft.studentIds || [],
        note: String(draft.note || '').trim() || null,
        notify: draft.notify !== false,
      };
      try {
        if (editing) return await app.api.patch(`/v1/admin/lesson-events/${event.id}`, common);
        return await app.api.post('/v1/admin/lesson-events', {
          ...common,
          kind: draft.kind || kind,
          subjectId: draft.subjectId || null,
          classGroupIds: draft.classGroupIds || [],
        }, { key });
      } catch (failure) {
        if (failure instanceof AkademiError && failure.code === 'invalid_range') {
          throw new AkademiError({ code: failure.code, message: problem(failure, 'Saat aralığı geçersiz.') });
        }
        throw readableConflict(failure, layout);
      }
    },
  });
  for (const fn of disposers.splice(0)) fn();
  return result === true ? null : result;
}

/** Gün + zil dilimi ya da elle saat aralığı → {day, from, to}. */
function timeField(layout, initial, disposers) {
  return (draft, set) => {
    const state = { day: initial?.day || todayIst(), from: initial?.from || '', to: initial?.to || '' };
    const wrap = h('div', 'ak-tt-when');
    const sync = () => set({ ...state });
    const day = dateInput(state.day, (iso) => {
      if (!iso || iso === state.day) return;
      state.day = iso;
      fill();
      sync();
    }, 'Gün');
    disposers.push(() => day.destroy());
    const slotSelect = h('select', 'kit-select');
    slotSelect.setAttribute('aria-label', 'Zil dilimi');
    const from = clockInput(state.from, (clock) => { state.from = clock; matchSlot(); sync(); }, 'Başlangıç');
    const to = clockInput(state.to, (clock) => { state.to = clock; matchSlot(); sync(); }, 'Bitiş');
    const quick = h('span', 'ak-tt-when-day');

    function lessons() {
      const key = dayKeyOf(state.day);
      return key ? layout.lessonsOf(key) : [];
    }
    function fill() {
      const list = lessons();
      slotSelect.replaceChildren();
      const manual = h('option', '', list.length ? 'Saati elle yaz' : 'Bu gün zil dilimi yok — saati elle yaz');
      manual.value = '';
      slotSelect.append(manual);
      for (const { row, slot } of list) {
        const option = h('option', '', `${row.label} · ${slotTime(slot)}`);
        option.value = slot.id;
        slotSelect.append(option);
      }
      matchSlot();
      quick.textContent = `${fmtDate(state.day)} ${SHORT_DAY[dayKeyOf(state.day)] || ''}`;
    }
    function matchSlot() {
      const hit = lessons().find(({ slot }) => slot.startTime === state.from && slot.endTime === state.to);
      slotSelect.value = hit ? hit.slot.id : '';
    }
    slotSelect.addEventListener('change', () => {
      const hit = lessons().find(({ slot }) => slot.id === slotSelect.value);
      if (!hit) return;
      state.from = hit.slot.startTime;
      state.to = hit.slot.endTime;
      from.node.value = state.from;
      to.node.value = state.to;
      from.node.classList.remove('bad');
      to.node.classList.remove('bad');
      sync();
    });
    for (const [input, part] of [[from, 'from'], [to, 'to']]) {
      input.node.addEventListener('input', () => {
        state[part] = input.value;
        matchSlot();
        sync();
      });
    }
    const clocks = h('span', 'ak-inline');
    clocks.append(from.node, h('span', '', '–'), to.node);
    wrap.append(day.node, slotSelect, clocks, quick);
    fill();
    sync();
    return wrap;
  };
}

/** Çoklu öğrenci seçimi: arama + çıkarılabilir çipler. */
function studentsField(app, initial, disposers) {
  return (draft, set) => {
    const picked = new Map((initial || []).map((student) => [student.id, student]));
    const wrap = h('div', 'ak-stack');
    const chips = h('div', 'ak-tt-people');
    const picker = studentPicker(app, { placeholder: 'Öğrenci ekle: ad ya da okul no', onPick: (student) => {
      picked.set(student.id, student);
      const input = picker.node.querySelector('input');
      if (input) input.value = '';
      paint();
    } });
    disposers.push(() => picker.destroy());
    function paint() {
      chips.replaceChildren();
      if (!picked.size) chips.append(h('span', 'ak-muted', 'Henüz öğrenci eklenmedi.'));
      for (const student of picked.values()) {
        const chip = h('span', 'ak-tt-person');
        chip.append(h('span', '', student.displayName || student.fullName || 'Öğrenci'));
        if (student.classGroup?.name) chip.append(h('small', '', student.classGroup.name));
        const remove = h('button', 'ak-tt-person-x', '×');
        remove.type = 'button';
        remove.setAttribute('aria-label', `${student.displayName || 'Öğrenci'} listeden çıkar`);
        remove.addEventListener('click', () => {
          picked.delete(student.id);
          paint();
        });
        chip.append(remove);
        chips.append(chip);
      }
      set([...picked.keys()]);
    }
    wrap.append(picker.node, chips);
    paint();
    return wrap;
  };
}

