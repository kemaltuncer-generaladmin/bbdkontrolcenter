// BBD Akademi — Ders programı › Haftalık program ızgarası (F-A5).
//
// TASLAK → TEK KAYIT. Hücreye tıklanınca sağdaki düzenleyicide ders, öğretmen
// ve derslik seçilir; değişiklik yalnız ekrandaki taslağa yazılır. "Kaydet"
// bütün haftayı tek `PUT` ile gönderir ve iyimser kilit taşır
// (`expectedVersion`). Ok tuşlarıyla hücreler arasında gezilir, Delete hücreyi
// boşaltır.
//
// ÇAKIŞMA AKADEMİ'DE DENETLENİR. Öğretmen ya da derslik aynı saatte başka
// sınıfta (ya da etütte) doluysa `409 timetable_conflict` döner ve HİÇBİR ŞEY
// yazılmaz; `details` ilgili hücrelere işlenir (kırmızı), kullanıcı düzeltir ve
// yeniden kaydeder. Sürüm uyuşmazlığında (`version_conflict`) kullanıcının
// değiştirdiği hücreler güncel programın üstüne hücre hücre yeniden uygulanabilir.
//
// ZİL SAATİ DEĞİŞİNCE. Akademi eşleşmeyen satırları `orphaned` işaretler.
// Dilimi hâlâ duruyorsa satır hücresinde uyarıyla görünür; dilimi artık yoksa
// ızgaranın üstünde listelenir ve kaydetmeden önce yerleştirilmesi ya da
// kaldırılması gerekir (aksi hâlde Akademi `unknown_bell_slot` der).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, skeletonRows } from '../../ui-kit/layout.js';
import {
  DAY_LABEL, actionButton, confirmAction, dateInput, empty, errorBox, fmtDate, fmtDateTime,
  formDialog, kv, num, problem, selectBox, todayIst, toolbar,
} from './common.js';
import { copyWeekDialog } from './timetable-copy.js';
import {
  cellKey, conflictText, slotLayout, slotTime, subjectHue, subjectIndex, subjectSelect,
  teacherOptions,
} from './timetable-shared.js';

/** Etiketli küçük alan (araç şeridi için). */
function field(label, control) {
  const wrap = h('label', 'ak-tt-field');
  wrap.append(h('span', 'ak-tt-field-label', label), control);
  return wrap;
}

const splitKey = (key) => {
  const at = String(key).indexOf('|');
  return [key.slice(0, at), key.slice(at + 1)];
};

/**
 * @param {object} app
 * @param {{openEvent: (id: string) => void, openBells: () => void}} links
 */
export function weekTab(app, { openEvent, openBells } = {}) {
  const node = h('div', 'ak-stack ak-tt-week');
  const editable = app.can('admin_putTimetable');
  const s = {
    started: false,
    classGroups: [],
    subjects: [],
    subjectsById: new Map(),
    teachers: [],
    bells: null,
    bellsStale: false,
    layout: slotLayout([]),
    classGroupId: '',
    viewDate: todayIst(),
    loaded: null,             // son okunan program (sunucu yanıtı)
    original: new Map(),      // hücre anahtarı → giriş (sunucudaki)
    draft: new Map(),         // hücre anahtarı → giriş (ekrandaki)
    orphans: [],              // dilimi artık olmayan satırlar
    orphansLoaded: 0,
    orphanIdsLoaded: [],
    baseFrom: '',
    baseTo: null,
    validFrom: '',
    validTo: null,
    version: 0,
    conflicts: new Map(),     // hücre anahtarı → [TimetableConflict]
    conflictList: [],         // [{key, item}]
    versionConflict: false,
    selected: null,
    saving: false,
  };
  const disposers = [];
  let token = 0;

  // ------------------------------------------------------------ araç şeridi
  const classSelect = selectBox([{ value: '', label: 'Sınıflar yükleniyor…' }], '',
    (value) => pickClass(value), { label: 'Sınıf' });
  const viewDate = dateInput(s.viewDate, (iso) => changeViewDate(iso), 'Gösterilen gün');
  disposers.push(() => viewDate.destroy());
  const copyButton = actionButton(app, 'admin_copyTimetable', 'Haftayı kopyala', {
    onClick: () => copyWeek() });
  node.append(toolbar(field('Sınıf', classSelect.node), field('Gösterilen gün', viewDate.node),
    h('span', 'kit-spacer'), copyButton));

  // ------------------------------------------------------- kayıt şeridi
  const bar = h('div', 'ak-tt-bar');
  const fromField = dateInput('', (iso) => {
    if (!iso || iso === s.validFrom) return;
    s.validFrom = iso;
    paintBar();
  }, 'Geçerlilik başlangıcı');
  const toField = dateInput('', (iso) => {
    if (!iso || iso === s.validTo) return;
    s.validTo = iso;
    openEnded.checked = false;
    paintBar();
  }, 'Geçerlilik bitişi');
  disposers.push(() => fromField.destroy(), () => toField.destroy());
  const openEnded = h('input', 'kit-check');
  openEnded.type = 'checkbox';
  openEnded.addEventListener('change', () => {
    s.validTo = openEnded.checked ? null : (s.baseTo || s.validFrom);
    toField.set(s.validTo || '');
    toWrap.classList.toggle('ak-tt-off', openEnded.checked);
    paintBar();
  });
  const openLabel = h('label', 'ak-tt-check');
  openLabel.append(openEnded, h('span', '', 'Süresiz'));
  const toWrap = h('span', 'ak-tt-to');
  toWrap.append(toField.node);
  const validity = h('div', 'ak-tt-validity');
  const validityText = h('span', 'ak-tt-validity-text');
  if (editable) {
    validity.append(h('span', 'ak-tt-field-label', 'Geçerlilik'), fromField.node, h('span', '', '–'),
      toWrap, openLabel);
  } else {
    validity.append(h('span', 'ak-tt-field-label', 'Geçerlilik'), validityText);
  }
  const meta = h('span', 'ak-tt-meta');
  const dirtyText = h('span', 'ak-tt-dirty');
  const revertButton = button('Vazgeç', { variant: 'ghost', onClick: () => revert() });
  const saveButton = actionButton(app, 'admin_putTimetable', 'Kaydet', { variant: 'primary',
    onClick: () => save() });
  bar.append(validity, meta, h('span', 'kit-spacer'), dirtyText, revertButton, saveButton);

  const alerts = h('div', 'ak-stack ak-tt-alerts');
  const gridWrap = h('div', 'ak-tt-gridwrap');
  const side = h('aside', 'ak-tt-side');
  const body = h('div', 'ak-tt-body');
  body.append(gridWrap, side);
  const summary = h('div', 'ak-tt-summary');
  const content = h('div', 'ak-stack');
  node.append(content);

  gridWrap.addEventListener('keydown', onGridKey);

  // ------------------------------------------------------------- yükleme
  async function start() {
    if (s.started) return;
    s.started = true;
    content.replaceChildren(skeletonRows(8, 6));
    let classGroups;
    try {
      const [groups, bells, subjects, teachers] = await Promise.all([
        app.lookups.classGroups(),
        app.lookups.bellSlots(),
        app.lookups.subjects().catch(() => []),
        app.lookups.teachers().catch(() => []),
      ]);
      classGroups = groups;
      s.bells = bells;
      s.layout = slotLayout(bells?.slots || []);
      s.subjects = subjects;
      s.subjectsById = subjectIndex(subjects);
      s.teachers = teachers;
    } catch (failure) {
      s.started = false;
      content.replaceChildren(errorBox(failure, () => start()));
      return;
    }
    s.classGroups = classGroups || [];
    if (!s.classGroupId) s.classGroupId = s.classGroups[0]?.id || '';
    paintClassOptions();
    if (!s.classGroupId) {
      content.replaceChildren(empty('Sınıf grubu yok',
        'Sınıf grupları KM sınıflarından eşitlenir. Öğrenciler bölümünden sınıfları kontrol edin.'));
      return;
    }
    await loadTimetable();
  }

  async function reloadBells() {
    app.api.invalidate('/v1/admin/bell-slots');
    const bells = await app.lookups.bellSlots();
    s.bells = bells;
    s.layout = slotLayout(bells?.slots || []);
    s.bellsStale = false;
  }

  async function loadTimetable() {
    const id = s.classGroupId;
    if (!id) return;
    const mine = ++token;
    content.replaceChildren(skeletonRows(8, Math.min(8, s.layout.days.length + 1 || 6)));
    try {
      if (s.bellsStale) await reloadBells();
      if (s.layout.empty) {
        if (mine !== token) return;
        content.replaceChildren(empty('Zil dilimi tanımlı değil',
          'Program zil dilimlerine hizalanır. Önce zil dilimlerini tanımlayın ya da KM\'den çekin.',
          [button('Zil dilimlerine git', { variant: 'primary', onClick: () => openBells?.() })]));
        return;
      }
      const data = await app.api.get(`/v1/admin/class-groups/${id}/timetable`, { date: s.viewDate });
      if (mine !== token) return;
      apply(data);
      paintClassOptions();
      content.replaceChildren(bar, alerts, body, summary);
      paintAll();
    } catch (failure) {
      if (mine !== token) return;
      content.replaceChildren(errorBox(failure, () => loadTimetable()));
    }
  }

  function fromServer(entry) {
    return {
      id: entry.id || null,
      dayOfWeek: entry.dayOfWeek,
      bellSlotId: entry.bellSlotId,
      subjectId: entry.subject?.id || '',
      subject: entry.subject || null,
      teacherId: entry.teacher?.id || '',
      teacher: entry.teacher || null,
      room: entry.room || '',
      status: entry.status || 'active',
    };
  }

  function apply(data) {
    s.loaded = data || {};
    s.version = Number(data?.version ?? 0);
    s.baseFrom = data?.validFrom || s.viewDate;
    s.baseTo = data?.validTo || null;
    s.validFrom = s.baseFrom;
    s.validTo = s.baseTo;
    s.original = new Map();
    s.draft = new Map();
    s.orphans = [];
    for (const entry of data?.entries || []) {
      const item = fromServer(entry);
      const key = cellKey(item.dayOfWeek, item.bellSlotId);
      const place = s.layout.place(item.dayOfWeek, item.bellSlotId);
      if (place && place.slot.kind === 'lesson') {
        s.original.set(key, item);
        s.draft.set(key, { ...item });
      } else {
        s.orphans.push(item);
      }
    }
    s.orphansLoaded = s.orphans.length;
    s.orphanIdsLoaded = s.orphans.map((item) => item.id).filter(Boolean);
    s.conflicts = new Map();
    s.conflictList = [];
    s.versionConflict = false;
    if (s.selected && !placeOf(s.selected)) s.selected = null;
    fromField.set(s.validFrom);
    toField.set(s.validTo || '');
    openEnded.checked = !s.validTo;
    toWrap.classList.toggle('ak-tt-off', !s.validTo);
  }

  function paintClassOptions() {
    const list = s.classGroups.map((item) => ({ value: item.id, label: item.name }));
    const loaded = s.loaded?.classGroup;
    if (loaded?.id && !list.some((item) => item.value === loaded.id)) {
      list.push({ value: loaded.id, label: `${loaded.name} (pasif)` });
    }
    if (!list.length) list.push({ value: '', label: 'Sınıf yok' });
    classSelect.options(list);
    classSelect.set(s.classGroupId);
  }

  // ------------------------------------------------------------- durum
  const sig = (entry) => (entry ? `${entry.subjectId}|${entry.teacherId || ''}|${String(entry.room || '').trim()}` : '');

  function dirtyKeys() {
    const keys = new Set([...s.original.keys(), ...s.draft.keys()]);
    return [...keys].filter((key) => sig(s.original.get(key)) !== sig(s.draft.get(key)));
  }
  function validityDirty() {
    return Boolean(s.loaded) && (s.validFrom !== s.baseFrom || (s.validTo || null) !== (s.baseTo || null));
  }
  function isDirty() {
    return Boolean(s.loaded) && (dirtyKeys().length > 0 || s.orphans.length !== s.orphansLoaded
      || validityDirty());
  }

  function placeOf(key) {
    const [day, slotId] = splitKey(key);
    const place = s.layout.place(day, slotId);
    return place && place.slot.kind === 'lesson' ? { day, ...place } : null;
  }

  function className() {
    return s.loaded?.classGroup?.name
      || s.classGroups.find((item) => item.id === s.classGroupId)?.name || 'Sınıf';
  }

  function classGrade() {
    return s.classGroups.find((item) => item.id === s.classGroupId)?.gradeLevel ?? null;
  }

  function teacherName(entry) {
    if (!entry?.teacherId) return '';
    return entry.teacher?.displayName
      || s.teachers.find((item) => item.id === entry.teacherId)?.displayName || '';
  }

  function subjectName(entry, short = false) {
    const subject = s.subjectsById.get(entry?.subjectId) || entry?.subject;
    if (!subject) return entry?.subjectId || '';
    return short ? (subject.shortName || subject.name) : subject.name;
  }

  // ---------------------------------------------------------------- çizim
  function paintAll() {
    paintGrid();
    paintSide();
    paintBar();
    paintAlerts();
    paintSummary();
  }

  function paintGrid() {
    const { days, rows } = s.layout;
    const hadFocus = gridWrap.contains(document.activeElement);
    const grid = h('div', 'ak-tt-grid');
    grid.style.gridTemplateColumns = `112px repeat(${days.length}, minmax(108px, 1fr))`;
    grid.setAttribute('role', 'grid');
    grid.setAttribute('aria-label', `${className()} haftalık ders programı`);
    grid.append(h('div', 'ak-tt-corner'));
    for (const day of days) grid.append(h('div', 'ak-tt-dayhead', DAY_LABEL[day] || day));
    const changed = new Set(dirtyKeys());
    // Gezinme tek sekme durağıdır (roving tabindex): seçili hücre ya da ilk ders hücresi.
    const firstDay = days.find((day) => s.layout.lessonsOf(day).length);
    const firstSlot = firstDay ? s.layout.lessonsOf(firstDay)[0].slot : null;
    const stop = s.selected || (firstSlot ? cellKey(firstDay, firstSlot.id) : null);
    for (const row of rows) {
      const head = h('div', `ak-tt-rowhead${row.isBreak ? ' ak-tt-is-break' : ''}`);
      head.append(h('b', '', row.label), h('small', '', slotTime(row.ref)));
      grid.append(head);
      for (const day of days) grid.append(cellNode(day, row, changed, stop));
    }
    gridWrap.replaceChildren(grid);
    if (hadFocus && s.selected) focusCell(s.selected);
  }

  function cellNode(day, row, changed, stop) {
    const slot = row.cells[day];
    if (!slot) {
      const none = h('div', 'ak-tt-none');
      none.title = 'Bu gün bu sırada zil dilimi yok.';
      return none;
    }
    if (slot.kind === 'break') {
      const pause = h('div', 'ak-tt-break', slot.label || 'Teneffüs');
      if (slotTime(slot) !== slotTime(row.ref)) pause.append(h('small', '', slotTime(slot)));
      return pause;
    }
    const key = cellKey(day, slot.id);
    const entry = s.draft.get(key);
    const conflicts = s.conflicts.get(key);
    const classes = ['ak-tt-cell'];
    if (entry) classes.push('ak-tt-is-filled');
    if (changed.has(key)) classes.push('ak-tt-is-dirty');
    if (conflicts?.length) classes.push('ak-tt-is-conflict');
    if (entry?.status === 'orphaned' && !changed.has(key)) classes.push('ak-tt-is-orphan');
    if (s.selected === key) classes.push('on');
    const cell = h('button', classes.join(' '));
    cell.type = 'button';
    cell.dataset.key = key;
    cell.tabIndex = key === stop ? 0 : -1;
    if (entry) {
      cell.style.setProperty('--ak-tt-hue', String(subjectHue(entry.subjectId)));
      cell.append(h('b', 'ak-tt-subject', subjectName(entry, true)));
      const teacher = teacherName(entry);
      if (teacher) cell.append(h('span', 'ak-tt-teacher', teacher));
      if (entry.room) cell.append(h('span', 'ak-tt-room', entry.room));
    } else if (editable) {
      cell.append(h('span', 'ak-tt-add', '+'));
    }
    if (slotTime(slot) !== slotTime(row.ref)) cell.append(h('span', 'ak-tt-time', slotTime(slot)));
    if (conflicts?.length) cell.append(h('span', 'ak-tt-flag', 'Çakışma'));
    const what = entry ? `${subjectName(entry)}${teacherName(entry) ? `, ${teacherName(entry)}` : ''}` : 'boş';
    cell.setAttribute('aria-label', `${DAY_LABEL[day]} ${row.label} ${slotTime(slot)}: ${what}`);
    cell.title = conflicts?.length ? conflicts.map((item) => conflictText(item, s.layout)).join('\n')
      : (entry ? `${subjectName(entry)}${entry.status === 'orphaned' ? ' — zil saati değişti, eşleşmiyor' : ''}` : '');
    cell.addEventListener('click', () => select(key));
    cell.addEventListener('dblclick', () => side.querySelector('select')?.focus());
    return cell;
  }

  function focusCell(key) {
    const escape = globalThis.CSS?.escape || ((value) => value.replace(/"/g, '\\"'));
    gridWrap.querySelector(`[data-key="${escape(key)}"]`)?.focus();
  }

  function select(key) {
    if (s.selected === key) return;
    s.selected = key;
    paintGrid();
    paintSide();
    focusCell(key);
  }

  /** Ok tuşları hücreler arasında gezer; Delete hücreyi boşaltır; Enter düzenleyiciye geçer. */
  function onGridKey(event) {
    if (!event.target.closest?.('.ak-tt-cell')) return;
    const key = event.target.dataset.key;
    const place = placeOf(key);
    if (!place) return;
    const days = s.layout.days;
    let dayIndex = days.indexOf(place.day);
    let rowIndex = place.row.index;
    const lessonAt = (day, index) => {
      const slot = s.layout.rows[index]?.cells[day];
      return slot && slot.kind === 'lesson' ? cellKey(day, slot.id) : null;
    };
    let target = null;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const step = event.key === 'ArrowLeft' ? -1 : 1;
      for (dayIndex += step; dayIndex >= 0 && dayIndex < days.length && !target; dayIndex += step) {
        target = lessonAt(days[dayIndex], rowIndex);
      }
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const step = event.key === 'ArrowUp' ? -1 : 1;
      for (rowIndex += step; rowIndex >= 0 && rowIndex < s.layout.rows.length && !target; rowIndex += step) {
        target = lessonAt(place.day, rowIndex);
      }
    } else if ((event.key === 'Delete' || event.key === 'Backspace') && editable) {
      event.preventDefault();
      s.selected = key;
      clearCell(key);
      return;
    } else if (event.key === 'Enter' && s.selected === key) {
      event.preventDefault();
      side.querySelector('select')?.focus();
      return;
    } else {
      return;
    }
    event.preventDefault();
    if (target) select(target);
  }

  // ----------------------------------------------------------- düzenleyici
  let sideRefs = null;

  function legend() {
    const box = h('div', 'ak-tt-legend');
    for (const [cls, text] of [['ak-tt-is-dirty', 'Değişti (kaydedilmedi)'], ['ak-tt-is-conflict', 'Çakışma'],
      ['ak-tt-is-orphan', 'Zil saati değişti, eşleşmiyor']]) {
      const item = h('span', 'ak-tt-legend-item');
      item.append(h('span', `ak-tt-swatch ${cls}`), h('span', '', text));
      box.append(item);
    }
    return box;
  }

  function paintSide() {
    sideRefs = null;
    side.replaceChildren();
    const place = s.selected ? placeOf(s.selected) : null;
    if (!place) {
      side.append(h('div', 'ak-tt-side-title', editable ? 'Hücre düzenleyici' : 'Hücre ayrıntısı'),
        h('p', 'ak-muted ak-tt-side-hint', editable
          ? 'Izgaradan bir hücre seçin. Ok tuşlarıyla gezinebilir, Delete ile hücreyi boşaltabilirsiniz. '
            + 'Değişiklikler “Kaydet”e basınca birlikte gönderilir.'
          : 'Bir hücre seçin; ayrıntısı burada görünür. Programı değiştirme yetkiniz yok.'),
        legend());
      return;
    }
    const key = s.selected;
    const entry = s.draft.get(key);
    side.append(h('div', 'ak-tt-side-title', `${DAY_LABEL[place.day]} · ${place.row.label}`),
      h('div', 'ak-tt-side-sub', `${slotTime(place.slot)} · ${className()}`));
    const conflictSlot = h('div', 'ak-stack');
    side.append(conflictSlot);
    const info = h('div', 'ak-tt-side-info');

    if (!editable) {
      side.append(kv([
        ['Ders', entry ? subjectName(entry) : 'Boş'],
        ['Öğretmen', teacherName(entry) || '—'],
        ['Derslik', entry?.room || '—'],
      ]), info);
      sideRefs = { conflictSlot, info };
      paintSideState();
      return;
    }

    const subject = subjectSelect(s.subjects, entry?.subjectId || '', {
      gradeLevel: classGrade(), extra: entry?.subject, empty: '— Boş (ders yok) —',
      onChange: (value) => update({ subjectId: value }),
    });
    const teacher = selectBox(teacherOptions(s.teachers, entry?.teacher), entry?.teacherId || '',
      (value) => update({ teacherId: value }), { label: 'Öğretmen' });
    const room = h('input', 'kit-input');
    room.type = 'text';
    room.maxLength = 40;
    room.placeholder = 'ör. 204';
    room.value = entry?.room || '';
    room.setAttribute('aria-label', 'Derslik');
    room.addEventListener('input', () => update({ room: room.value }));
    const clear = button('Hücreyi boşalt', { variant: 'ghost', onClick: () => clearCell(key) });
    const form = h('div', 'ak-tt-side-form');
    form.append(field('Ders', subject), field('Öğretmen', teacher.node), field('Derslik', room));
    const actions = h('div', 'ak-inline');
    actions.append(clear);
    side.append(form, actions, info);
    sideRefs = { conflictSlot, info, subject, teacher, room, clear };
    paintSideState();
  }

  /** Düzenleyicinin seçimden bağımsız parçaları: çakışma kutusu, bilgi, kapalı alanlar. */
  function paintSideState() {
    if (!sideRefs || !s.selected) return;
    const key = s.selected;
    const entry = s.draft.get(key);
    const { conflictSlot, info } = sideRefs;
    conflictSlot.replaceChildren();
    for (const item of s.conflicts.get(key) || []) {
      conflictSlot.append(alertBox(conflictText(item, s.layout), 'bad'));
    }
    if (entry?.status === 'orphaned' && sig(entry) === sig(s.original.get(key))) {
      conflictSlot.append(alertBox('Zil saatleri değiştiği için Akademi bu satırı “eşleşmiyor” '
        + 'olarak işaretledi. Ders bu saatte doğruysa programı kaydedin; değilse düzeltin.', 'warn'));
    }
    if (sideRefs.teacher) {
      sideRefs.teacher.node.disabled = !entry;
      sideRefs.room.disabled = !entry;
      sideRefs.clear.disabled = !entry;
      if (!entry) {
        sideRefs.teacher.set('');
        sideRefs.room.value = '';
      }
    }
    info.replaceChildren();
    if (entry) {
      const sameSubject = [...s.draft.values()].filter((item) => item.subjectId === entry.subjectId).length;
      info.append(h('div', '', `Bu sınıfta haftada ${num(sameSubject)} saat ${subjectName(entry)}.`));
      if (entry.teacherId) {
        const load = [...s.draft.values()].filter((item) => item.teacherId === entry.teacherId).length;
        info.append(h('div', '', `${teacherName(entry)} bu sınıfta haftada ${num(load)} saat derste.`));
      }
    } else if (s.original.get(key)) {
      info.append(h('div', '', `Kaydedilince ${subjectName(s.original.get(key))} dersi bu saatten kalkar.`));
    }
  }

  function update(patch) {
    const key = s.selected;
    const place = placeOf(key);
    if (!place) return;
    const before = s.draft.get(key);
    const current = before || { id: s.original.get(key)?.id || null, dayOfWeek: place.day,
      bellSlotId: place.slot.id, subjectId: '', subject: null, teacherId: '', teacher: null,
      room: '', status: 'active' };
    const next = { ...current, ...patch };
    if (patch.subjectId !== undefined) {
      next.subject = s.subjectsById.get(patch.subjectId) || (current.subject?.id === patch.subjectId
        ? current.subject : null);
      // Boş hücreye ders seçilince: bu sınıfta aynı dersi veren öğretmen ve
      // derslik varsa önerilir (çoğu zaman aynı öğretmendir).
      if (patch.subjectId && !before) {
        const same = [...s.draft.values()].find((item) => item.subjectId === patch.subjectId && item.teacherId);
        if (same) {
          next.teacherId = same.teacherId;
          next.teacher = same.teacher;
          if (!next.room && same.room) next.room = same.room;
        }
      }
    }
    if (patch.teacherId !== undefined) {
      const known = s.teachers.find((item) => item.id === patch.teacherId);
      next.teacher = known ? { id: known.id, displayName: known.displayName }
        : (current.teacher?.id === patch.teacherId ? current.teacher : null);
    }
    if (!next.subjectId) s.draft.delete(key);
    else s.draft.set(key, next);
    if (sideRefs?.teacher && patch.subjectId !== undefined && next.subjectId) {
      sideRefs.teacher.set(next.teacherId || '');
      sideRefs.room.value = next.room || '';
    }
    clearConflict(key);
    paintGrid();
    paintSideState();
    paintBar();
    paintAlerts();
    paintSummary();
  }

  function clearCell(key) {
    if (!s.draft.has(key)) return;
    s.draft.delete(key);
    clearConflict(key);
    paintAll();
    focusCell(key);
  }

  function clearConflict(key) {
    if (!s.conflicts.has(key)) return;
    s.conflicts.delete(key);
    s.conflictList = s.conflictList.filter((item) => item.key !== key);
  }

  // ------------------------------------------------------------- şerit
  function paintBar() {
    const changed = dirtyKeys().length;
    const parts = [];
    if (changed) parts.push(`${num(changed)} hücre değişti`);
    if (s.orphans.length !== s.orphansLoaded) parts.push('eşleşmeyen satırlar düzenlendi');
    if (validityDirty()) parts.push('geçerlilik değişti');
    dirtyText.textContent = parts.length ? `Kaydedilmedi: ${parts.join(' · ')}` : 'Değişiklik yok';
    dirtyText.classList.toggle('on', parts.length > 0);
    revertButton.hidden = !parts.length;
    if (editable) {
      saveButton.disabled = s.saving || !parts.length || s.orphans.length > 0;
      saveButton.textContent = s.saving ? 'Kaydediliyor…' : 'Kaydet';
      saveButton.title = s.orphans.length ? 'Önce eşleşmeyen satırları yerleştirin ya da kaldırın.' : '';
    }
    validityText.textContent = `${fmtDate(s.validFrom)} – ${s.validTo ? fmtDate(s.validTo) : 'süresiz'}`;
    const by = s.loaded?.updatedAt
      ? ` · son değişiklik ${fmtDateTime(s.loaded.updatedAt)}${s.loaded.updatedBy ? `, ${s.loaded.updatedBy}` : ''}`
      : ' · henüz kaydedilmemiş program';
    meta.textContent = `Sürüm ${num(s.version)}${by}`;
  }

  function paintSummary() {
    summary.replaceChildren();
    const counts = new Map();
    for (const entry of s.draft.values()) counts.set(entry.subjectId, (counts.get(entry.subjectId) || 0) + 1);
    if (!counts.size) {
      summary.append(h('span', 'ak-muted', editable
        ? 'Bu programda henüz ders yok. Bir hücreye tıklayıp ders seçerek başlayın.'
        : 'Bu programda henüz ders yok.'));
      return;
    }
    const total = [...counts.values()].reduce((sum, value) => sum + value, 0);
    summary.append(h('span', 'ak-tt-summary-title', `Haftada ${num(total)} ders saati`));
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    for (const [subjectId, count] of sorted) {
      const chip = h('span', 'ak-tt-chip', `${subjectName({ subjectId })} · ${num(count)}`);
      chip.style.setProperty('--ak-tt-hue', String(subjectHue(subjectId)));
      summary.append(chip);
    }
  }

  // ----------------------------------------------------------- uyarılar
  function paintAlerts() {
    alerts.replaceChildren();
    if (s.versionConflict) alerts.append(versionBox());
    if (s.conflictList.length) alerts.append(conflictBox());
    if (s.orphans.length) alerts.append(orphanBox());
    if (s.bells?.source === 'km' && s.bells.available === false) {
      alerts.append(alertBox('KM Zil Sistemi\'ne şu an ulaşılamıyor; son eşitlenen zil saatleri '
        + `kullanılıyor${s.bells.syncedAt ? ` (${fmtDateTime(s.bells.syncedAt)})` : ''}.`, 'warn'));
    }
    alerts.hidden = !alerts.childElementCount;
  }

  function versionBox() {
    const box = h('div', 'ak-tt-notice warn');
    box.append(h('b', '', 'Program siz düzenlerken başka biri tarafından değiştirildi.'),
      h('span', '', 'Hiçbir şey kaydedilmedi. Değişikliklerinizi güncel programın üstüne uygulayıp '
        + 'kontrol ettikten sonra yeniden kaydedebilirsiniz.'));
    const tools = h('div', 'ak-inline');
    tools.append(
      button('Değişikliklerimi güncel programa uygula', { variant: 'primary', onClick: () => rebase() }),
      button('Güncel programı aç (değişikliklerim silinir)', { variant: 'ghost', onClick: () => {
        s.versionConflict = false;
        loadTimetable();
      } }),
    );
    box.append(tools);
    return box;
  }

  function conflictBox() {
    const box = h('div', 'ak-tt-notice bad');
    box.append(h('b', '', `${num(s.conflictList.length)} çakışma — hiçbir şey kaydedilmedi.`),
      h('span', '', 'Kırmızı hücrelerde öğretmeni ya da dersliği değiştirin, sonra yeniden kaydedin.'));
    const list = h('ul', 'ak-tt-issues');
    for (const { key, item } of s.conflictList) {
      const line = h('li');
      line.append(h('span', '', conflictText(item, s.layout)));
      const tools = h('span', 'ak-tt-issue-tools');
      if (placeOf(key)) {
        tools.append(button('Hücreye git', { variant: 'ghost', onClick: () => select(key) }));
      }
      if (item.conflictingLessonEventId && openEvent) {
        tools.append(button('Etüdü aç', { variant: 'ghost',
          onClick: () => openEvent(item.conflictingLessonEventId) }));
      } else if (item.conflictingClassGroup?.id && item.conflictingClassGroup.id !== s.classGroupId) {
        tools.append(button(`${item.conflictingClassGroup.name} programı`, { variant: 'ghost',
          onClick: () => pickClass(item.conflictingClassGroup.id) }));
      }
      line.append(tools);
      list.append(line);
    }
    box.append(list);
    return box;
  }

  function orphanBox() {
    const box = h('div', 'ak-tt-notice warn');
    box.append(h('b', '', `${num(s.orphans.length)} satır artık var olmayan bir zil dilimine bağlı.`),
      h('span', '', editable
        ? 'Zil saatleri değişti. Kaydetmeden önce her satırı bir hücreye yerleştirin ya da kaldırın.'
        : 'Zil saatleri değişti; bu satırların programda yeniden yerleştirilmesi gerekiyor.'));
    const list = h('ul', 'ak-tt-issues');
    s.orphans.forEach((item, index) => {
      const line = h('li');
      line.append(h('span', '', [DAY_LABEL[item.dayOfWeek] || item.dayOfWeek, subjectName(item),
        teacherName(item), item.room].filter(Boolean).join(' · ')));
      if (editable) {
        const tools = h('span', 'ak-tt-issue-tools');
        tools.append(
          button('Yerleştir', { variant: 'ghost', onClick: () => placeOrphan(index) }),
          button('Kaldır', { variant: 'ghost', onClick: () => {
            s.orphans.splice(index, 1);
            paintBar();
            paintAlerts();
          } }),
        );
        line.append(tools);
      }
      list.append(line);
    });
    box.append(list);
    return box;
  }

  async function placeOrphan(index) {
    const orphan = s.orphans[index];
    if (!orphan) return;
    const firstFree = s.layout.lessonsOf(orphan.dayOfWeek)
      .find(({ slot }) => !s.draft.has(cellKey(orphan.dayOfWeek, slot.id)));
    const initial = firstFree ? cellKey(orphan.dayOfWeek, firstFree.slot.id) : '';
    const target = await formDialog(app, {
      title: `${subjectName(orphan)} dersini yerleştir`,
      description: `Eski yeri: ${DAY_LABEL[orphan.dayOfWeek] || orphan.dayOfWeek}. Dolu bir hücre seçerseniz `
        + 'oradaki dersin yerine geçer. Değişiklik “Kaydet”e basınca gönderilir.',
      fields: [{ key: 'cell', label: 'Hücre', type: 'custom', required: true, render: (draft, set) => {
        const select = h('select', 'kit-select');
        select.setAttribute('aria-label', 'Hücre');
        const blank = h('option', '', 'Hücre seçin');
        blank.value = '';
        select.append(blank);
        for (const day of s.layout.days) {
          const group = h('optgroup');
          group.label = DAY_LABEL[day] || day;
          for (const { row, slot } of s.layout.lessonsOf(day)) {
            const key = cellKey(day, slot.id);
            const taken = s.draft.get(key);
            const option = h('option', '', `${row.label} (${slotTime(slot)})${taken ? ` — dolu: ${subjectName(taken)}` : ''}`);
            option.value = key;
            group.append(option);
          }
          select.append(group);
        }
        select.value = initial;
        set(initial);
        select.addEventListener('change', () => set(select.value));
        return select;
      } }],
      submitLabel: 'Yerleştir',
      onSubmit: (draft) => draft.cell,
    });
    if (!target) return;
    const place = placeOf(target);
    if (!place) return;
    s.orphans.splice(s.orphans.indexOf(orphan), 1);
    s.draft.set(target, { ...orphan, dayOfWeek: place.day, bellSlotId: place.slot.id, status: 'active' });
    s.selected = target;
    paintAll();
  }

  // ------------------------------------------------------------ kayıt
  async function save() {
    if (s.saving || !s.loaded) return;
    if (s.orphans.length) {
      app.toast('Önce eşleşmeyen satırları yerleştirin ya da kaldırın.', 'warn');
      return;
    }
    if (s.validTo && s.validTo < s.validFrom) {
      app.toast('Geçerlilik bitişi başlangıçtan önce olamaz.', 'bad');
      return;
    }
    const list = [...s.draft.entries()];
    const keys = list.map(([key]) => key);
    const entries = list.map(([, entry]) => ({
      id: entry.id || null,
      dayOfWeek: entry.dayOfWeek,
      bellSlotId: entry.bellSlotId,
      subjectId: entry.subjectId,
      teacherId: entry.teacherId || null,
      room: String(entry.room || '').trim() || null,
    }));
    s.saving = true;
    paintBar();
    try {
      const next = await app.api.put(`/v1/admin/class-groups/${s.classGroupId}/timetable`, {
        validFrom: s.validFrom,
        validTo: s.validTo || null,
        expectedVersion: s.version,
        entries,
      });
      apply(next);
      s.saving = false;
      paintAll();
      app.toast(`${className()} programı kaydedildi.`, 'good');
    } catch (failure) {
      s.saving = false;
      if (failure?.code === 'timetable_conflict' && Array.isArray(failure.details) && failure.details.length) {
        markConflicts(failure.details, keys);
        app.toast(`${num(failure.details.length)} çakışma var; hiçbir şey kaydedilmedi.`, 'bad');
      } else if (failure?.code === 'version_conflict') {
        s.versionConflict = true;
        paintAlerts();
        paintBar();
        app.toast('Program başka biri tarafından değiştirilmiş; kaydedilmedi.', 'warn');
      } else {
        paintBar();
        app.toast(problem(failure, 'Program kaydedilemedi.'), 'bad');
      }
    }
  }

  function markConflicts(details, keys) {
    s.conflicts = new Map();
    s.conflictList = [];
    for (const item of details) {
      let key = item.dayOfWeek && item.bellSlotId ? cellKey(item.dayOfWeek, item.bellSlotId) : null;
      if ((!key || !s.draft.has(key)) && Number.isInteger(item.entryIndex) && keys[item.entryIndex]) {
        key = keys[item.entryIndex];
      }
      if (!key) continue;
      s.conflicts.set(key, [...(s.conflicts.get(key) || []), item]);
      s.conflictList.push({ key, item });
    }
    const first = s.conflictList.find((entry) => placeOf(entry.key));
    if (first) s.selected = first.key;
    paintAll();
  }

  /** Sürüm uyuşmazlığı: güncel programı al, değiştirdiğim hücreleri üstüne uygula. */
  async function rebase() {
    const mine = new Map(dirtyKeys().map((key) => [key, s.draft.get(key) || null]));
    const validity = validityDirty() ? { from: s.validFrom, to: s.validTo } : null;
    const keptOrphans = new Set(s.orphans.map((item) => item.id));
    const removedOrphans = new Set(s.orphanIdsLoaded.filter((id) => !keptOrphans.has(id)));
    try {
      const latest = await app.api.get(`/v1/admin/class-groups/${s.classGroupId}/timetable`,
        { date: s.viewDate });
      apply(latest);
    } catch (failure) {
      app.toast(problem(failure, 'Güncel program alınamadı.'), 'bad');
      return;
    }
    for (const [key, entry] of mine) {
      if (entry) s.draft.set(key, { ...entry, id: s.original.get(key)?.id || entry.id || null });
      else s.draft.delete(key);
    }
    // Eşleşmeyen satırlardan kullanıcının kaldırdıkları ya da yerleştirdikleri
    // güncel programda da listeden çıkar.
    const placed = new Set([...s.draft.values()].map((item) => item.id).filter(Boolean));
    s.orphans = s.orphans.filter((item) => !removedOrphans.has(item.id) && !placed.has(item.id));
    if (validity) {
      s.validFrom = validity.from;
      s.validTo = validity.to;
      fromField.set(s.validFrom);
      toField.set(s.validTo || '');
      openEnded.checked = !s.validTo;
      toWrap.classList.toggle('ak-tt-off', !s.validTo);
    }
    paintAll();
    app.toast('Değişiklikleriniz güncel programa uygulandı; kontrol edip kaydedin.', 'good');
  }

  async function revert() {
    if (!isDirty()) return;
    const ok = await confirmAction(app, { title: 'Değişiklikleri geri al',
      description: 'Kaydedilmemiş bütün değişiklikler silinir ve son kaydedilen program gösterilir.',
      confirmLabel: 'Geri al', danger: true });
    if (!ok) return;
    apply(s.loaded);
    paintAll();
  }

  // ------------------------------------------------------- sınıf / gün
  async function leaveOk() {
    if (!isDirty()) return true;
    return confirmAction(app, { title: 'Kaydedilmemiş değişiklikler',
      description: `${className()} programında kaydedilmemiş değişiklikler var. Devam ederseniz kaybolur.`,
      confirmLabel: 'Değişiklikleri bırak', danger: true });
  }

  async function pickClass(id) {
    if (!id || (id === s.classGroupId && s.loaded)) {
      classSelect.set(s.classGroupId);
      return;
    }
    if (!(await leaveOk())) {
      classSelect.set(s.classGroupId);
      return;
    }
    s.classGroupId = id;
    s.selected = null;
    s.loaded = null;
    classSelect.set(id);
    if (!s.started) {
      start();
      return;
    }
    loadTimetable();
  }

  async function changeViewDate(iso) {
    if (!iso || iso === s.viewDate) return;
    if (!(await leaveOk())) {
      viewDate.set(s.viewDate);
      return;
    }
    s.viewDate = iso;
    if (s.started && s.classGroupId) loadTimetable();
  }

  // ------------------------------------------------------- haftayı kopyala
  async function copyWeek() {
    if (!s.loaded) {
      app.toast('Önce bir sınıfın programını açın.', 'warn');
      return;
    }
    const result = await copyWeekDialog(app, {
      source: { id: s.classGroupId, name: className() },
      sourceDate: s.viewDate,
      defaultFrom: s.validFrom,
      hours: s.original.size,
      dirty: isDirty(),
      classGroups: s.classGroups,
      layout: s.layout,
    });
    const own = result?.results?.some((item) => item.classGroupId === s.classGroupId && item.status === 'copied');
    if (own && !isDirty()) loadTimetable();
  }

  return {
    node,
    reload: () => start(),
    /** Derin bağlantı: bu sınıfın ızgarasını aç. */
    open(classGroupId) { pickClass(classGroupId); },
    /** Zil dilimleri değişti: önbellek düştü; açık ızgara güvenliyse yenilenir. */
    bellsChanged() {
      s.bellsStale = true;
      if (s.started && s.loaded && !isDirty()) loadTimetable();
    },
    dirty: () => isDirty(),
    destroy() {
      token += 1;
      for (const fn of disposers.splice(0)) fn();
    },
  };
}
