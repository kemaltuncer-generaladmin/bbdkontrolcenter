// BBD Akademi — Ders programı › HAFTA TAKVİMİ (Akademi README §14 madde 125, iş 69).
//
// KULLANICI (17.10.2026): "kontrol merkezinde ders programı girme kısmı çok karışık. hafta seçince o haftanın
// programı gelmeli işte öbür haftaya geçince boşalmalı" · "takvim olacak ekranda. bu hafta önceki hafta sonraki
// hafta diye ok tuşları olacak yanında. sınıf seçip program girecez şak şak".
//
// EKRAN. Üstte ‹ Önceki hafta · Bu hafta · Sonraki hafta › ve haftanın adı ("13–19 Ekim 2026"), yanında SINIF
// seçici. Ortada takvim: günler sütun (tarihli), zil dilimleri satır. Seçilen haftanın programı gelir; programı
// olmayan hafta boştur ("Bu hafta için program yok · Geçen haftayı kopyala · Şablondan doldur · Boş başla").
// Geçmiş günler salt okunur, tatil günü gri ("Tatil — ders yok"), çakışma hücrede kırmızı.
//
// ŞAK ŞAK. Boş hücreye tıkla (ya da Enter / harf yaz) → küçük pencere: ders, öğretmen (zorunlu), derslik; bu
// sınıfta o dersi veren öğretmen ve derslik kendiliğinden gelir, bu sınıfın dersleri listenin başındadır.
// Kaydet anında yazılır (taslak / "Kaydet" şeridi yok). Tek soru: "Yalnız bu hafta" (varsayılan) ya da "Bu hafta ve
// sonraki haftalar". Sürükle-bırak taşır (Ctrl / Alt ile kopyalar), Delete kaldırır, Ctrl+C / Ctrl+V kopyalar,
// PageUp / PageDown hafta değiştirir.
//
// KAVRAM. Kullanıcı geçerlilik aralığı, sürüm, oturum üretimi görmez: Akademi hücreyi o haftaya (ya da o
// haftadan sonrasına) yazar, satırı kendisi böler (`admin_putTimetableWeekCells`). Dönem / şablon ikincildir
// ("Diğer" menüsü). İş kuralı (çakışma, geçmiş gün, yoklamalı oturum) AKADEMİ'DEDİR; panel sonucu gösterir.

import { button, dialogA11y, h, menuButton } from '../../../ui-kit/kit.js';
import { alertBox, skeletonRows } from '../../../ui-kit/layout.js';
import { AkademiError } from '../api.js';
import {
  DAY_LABEL, WEEKDAYS, actionButton, addDays, dateInput, empty, errorBox, fmtDate, fmtDateTime, formDialog,
  num, problem, selectBox, todayIst, weekStart,
} from '../common.js';
import { runDocument } from '../documents.js';
import { classTimetableDoc, teacherTimetableDoc } from '../documents-logic.js';
import { fetchPrograms, fillProgramSelect } from '../shared/curriculum-picker.js';
import { rolloverCopyDialog } from './timetable-copy.js';
import {
  DAY_KEYS, cellKey, conflictText, slotLayout, slotTime, subjectHue, subjectIndex, subjectSelect, teacherOptions,
} from './timetable-shared.js';

const PUT_CELLS = 'admin_putTimetableWeekCells';
const COPY_WEEK = 'admin_copyTimetableWeek';
const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim',
  'Kasım', 'Aralık'];
const SHORT_MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
const SHORT_DAY = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));

export const SCOPES = [
  ['this_week', 'Yalnız bu hafta'],
  ['this_and_following', 'Bu hafta ve sonraki haftalar'],
];
export const SCOPE_DONE = { this_week: 'yalnız bu hafta', this_and_following: 'bu hafta ve sonraki haftalar' };
/** Kopya penceresinde gösterilen ileri hafta sayısı. */
export const COPY_WEEKS_SHOWN = 12;

// ------------------------------------------------------------------ saf yardımcılar (Node'da sınanır)

const parts = (iso) => String(iso).slice(0, 10).split('-').map(Number);

/** "13–19 Ekim 2026" · "27 Ekim – 2 Kasım 2026" · "29 Aralık 2025 – 4 Ocak 2026". */
export function weekLabel(monday) {
  const [y1, m1, d1] = parts(monday);
  const [y2, m2, d2] = parts(addDays(monday, 6));
  if (y1 !== y2) return `${d1} ${MONTHS[m1 - 1]} ${y1} – ${d2} ${MONTHS[m2 - 1]} ${y2}`;
  if (m1 !== m2) return `${d1} ${MONTHS[m1 - 1]} – ${d2} ${MONTHS[m2 - 1]} ${y2}`;
  return `${d1}–${d2} ${MONTHS[m2 - 1]} ${y2}`;
}

/** "20–26 Eki" (kopya çipleri). */
export function shortWeekLabel(monday) {
  const [, m1, d1] = parts(monday);
  const [, m2, d2] = parts(addDays(monday, 6));
  return m1 === m2 ? `${d1}–${d2} ${SHORT_MONTHS[m2 - 1]}` : `${d1} ${SHORT_MONTHS[m1 - 1]} – ${d2} ${SHORT_MONTHS[m2 - 1]}`;
}

/** "Pzt 13 Eki". */
export function dayHead(dayKey, iso) {
  const [, month, day] = parts(iso);
  return `${SHORT_DAY[dayKey] || dayKey} ${day} ${SHORT_MONTHS[month - 1]}`;
}

/** Haftanın bugüne göre adı: "Bu hafta", "Geçen hafta", "Gelecek hafta", "3 hafta sonra", "2 hafta önce". */
export function weekRelation(monday, today = todayIst()) {
  const [a, b] = [parts(monday), parts(weekStart(today))];
  const diff = Math.round((Date.UTC(a[0], a[1] - 1, a[2]) - Date.UTC(b[0], b[1] - 1, b[2])) / (7 * 86_400_000));
  if (diff === 0) return 'Bu hafta';
  if (diff === -1) return 'Geçen hafta';
  if (diff === 1) return 'Gelecek hafta';
  return diff > 0 ? `${diff} hafta sonra` : `${-diff} hafta önce`;
}

/** Sunucu hücresi → yazma hücresi (taşıma / kopyalama / yapıştırma). Ek sınıf yoksa `classGroupIds` gönderilmez. */
export function cellOf(entry, { dayOfWeek, bellSlotId }, ownerId) {
  const cell = {
    dayOfWeek,
    bellSlotId,
    subjectId: entry.subject?.id || entry.subjectId || null,
    teacherId: entry.teacher?.id || entry.teacherId || null,
    room: entry.room || null,
  };
  const extra = (entry.classGroups || []).map((group) => group?.id).filter((id) => id && id !== ownerId);
  if (extra.length) cell.classGroupIds = [ownerId, ...extra];
  const programId = entry.program?.id || entry.programId;
  if (programId) cell.programId = programId;
  return cell;
}

/** Boş hücre (kaldırma). */
export const clearCell = ({ dayOfWeek, bellSlotId }) => ({ dayOfWeek, bellSlotId, subjectId: null });

/**
 * Şablon satırları → hücreler. Zil düzeninde olmayan (ya da teneffüs) dilimler atlanır ve sayılır.
 * @returns {{cells: object[], skipped: number}}
 */
export function templateCells(entries, layout, ownerId) {
  const cells = [];
  let skipped = 0;
  const seen = new Set();
  for (const entry of entries || []) {
    const slot = layout.slot(entry.dayOfWeek, entry.bellSlotId);
    const key = cellKey(entry.dayOfWeek, entry.bellSlotId);
    if (!slot || slot.kind !== 'lesson' || seen.has(key) || !entry.subjectId) {
      skipped += 1;
      continue;
    }
    seen.add(key);
    const cell = { dayOfWeek: entry.dayOfWeek, bellSlotId: entry.bellSlotId, subjectId: entry.subjectId,
      teacherId: entry.teacherId || null, room: entry.room || null };
    const extra = (entry.classGroupIds || []).filter((id) => id && id !== ownerId);
    if (extra.length) cell.classGroupIds = [ownerId, ...extra];
    if (entry.programId) cell.programId = entry.programId;
    cells.push(cell);
  }
  return { cells, skipped };
}

/** Haftadaki derslerin sıklığa göre ders kimlikleri (seçicide "Bu sınıfta" grubu). */
export function frequentSubjects(entries) {
  const counts = new Map();
  for (const entry of entries || []) {
    const id = entry.subject?.id;
    if (id) counts.set(id, (counts.get(id) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
}

/** Bu dersi bu sınıfta en çok veren öğretmen ve derslik (öneri). */
export function suggestionFor(entries, subjectId, memory = new Map()) {
  const tally = new Map();
  for (const entry of entries || []) {
    if (entry.subject?.id !== subjectId || !entry.teacher?.id) continue;
    const key = `${entry.teacher.id}|${entry.room || ''}`;
    tally.set(key, (tally.get(key) || 0) + 1);
  }
  const best = [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  if (best) {
    const [teacherId, room] = best.split('|');
    return { teacherId, room: room || '' };
  }
  return memory.get(subjectId) || null;
}

/** Kopya sonucu → tek cümle. */
export function copySummary(results) {
  const copied = (results || []).filter((item) => item.status === 'copied');
  const conflict = (results || []).filter((item) => item.status === 'conflict');
  const past = (results || []).filter((item) => item.status === 'past');
  const bits = [];
  if (copied.length) bits.push(`${num(copied.length)} haftaya kopyalandı`);
  if (conflict.length) {
    bits.push(`${num(conflict.length)} haftada çakışma var, yazılmadı (${conflict
      .map((item) => shortWeekLabel(item.weekStart)).join(', ')})`);
  }
  if (past.length) bits.push(`${num(past.length)} geçmiş hafta atlandı`);
  return bits.length ? `${bits.join(' · ')}.` : 'Hiçbir hafta değişmedi.';
}

// ------------------------------------------------------------------ küçük pencereler

function dialogShell(app, title, description = '') {
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog ak-dialog ak-cal-dialog');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.append(h('h3', 'kit-dialog-title', title));
  if (description) card.append(h('p', 'kit-dialog-text', description));
  overlay.append(card);
  return { overlay, card };
}

/**
 * Tek soru: "Yalnız bu hafta" (varsayılan, odakta — Enter) / "Bu hafta ve sonraki haftalar" / Vazgeç.
 * @returns {Promise<'this_week'|'this_and_following'|null>}
 */
export function askScope(app, { title, description = '', verb = '' }) {
  return new Promise((resolve) => {
    const { overlay, card } = dialogShell(app, title, description);
    let release = () => {};
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') { event.stopPropagation(); close(null); } };
    const actions = h('div', 'kit-dialog-actions');
    const only = button(`${verb ? `${verb} · ` : ''}Yalnız bu hafta`, { variant: 'primary', onClick: () => close('this_week') });
    actions.append(button('Vazgeç', { onClick: () => close(null) }),
      button('Bu hafta ve sonraki haftalar', { onClick: () => close('this_and_following') }), only);
    card.append(actions);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    only.focus();
  });
}

/** "Yalnız bu hafta / Bu hafta ve sonraki haftalar" radyo grubu (varsayılan ilk). */
function scopeRadios(name, value = 'this_week') {
  const node = h('div', 'ak-cal-scope');
  node.setAttribute('role', 'radiogroup');
  node.setAttribute('aria-label', 'Hangi haftalar');
  const inputs = [];
  for (const [key, label] of SCOPES) {
    const wrap = h('label', 'ak-cal-scope-item');
    const input = h('input');
    input.type = 'radio';
    input.name = name;
    input.value = key;
    input.checked = key === value;
    inputs.push(input);
    wrap.append(input, h('span', '', label));
    node.append(wrap);
  }
  return { node, get value() { return inputs.find((input) => input.checked)?.value || 'this_week'; } };
}

let dialogSeq = 0;

// ------------------------------------------------------------------ ekran

/**
 * @param {object} app
 * @param {{openBells?: () => void}} links
 */
export function calendarTab(app, { openBells } = {}) {
  const node = h('div', 'ak-stack ak-cal');
  const editable = app.can(PUT_CELLS);
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
    week: weekStart(todayIst()),
    data: null,
    clip: null,              // Ctrl+C ile alınan hücre (sunucu girişi)
    focus: null,             // odaklı hücre anahtarı
    memory: new Map(),       // ders → {teacherId, room} (bu oturumda son girilen)
    busy: false,
  };
  const disposers = [];
  let token = 0;

  // ------------------------------------------------------------- araç şeridi
  const prev = button('‹ Önceki hafta', { onClick: () => goWeek(addDays(s.week, -7)) });
  prev.setAttribute('aria-label', 'Önceki hafta');
  const thisWeek = button('Bu hafta', { onClick: () => goWeek(weekStart(todayIst())) });
  const next = button('Sonraki hafta ›', { onClick: () => goWeek(addDays(s.week, 7)) });
  next.setAttribute('aria-label', 'Sonraki hafta');
  const nav = h('div', 'ak-cal-nav');
  nav.setAttribute('role', 'group');
  nav.setAttribute('aria-label', 'Hafta');
  nav.append(prev, thisWeek, next);
  const title = h('div', 'ak-cal-title');
  const titleText = h('b', 'ak-cal-week', weekLabel(s.week));
  const titleRel = h('span', 'ak-cal-rel', weekRelation(s.week));
  title.append(titleText, titleRel);
  title.setAttribute('aria-live', 'polite');
  const jump = dateInput(s.week, (iso) => { if (iso) goWeek(weekStart(iso)); }, 'Takvimden hafta seç');
  disposers.push(() => jump.destroy());
  const jumpWrap = h('span', 'ak-cal-jump');
  jumpWrap.append(jump.node);
  const classSelect = selectBox([{ value: '', label: 'Sınıflar yükleniyor…' }], '', (value) => pickClass(value),
    { label: 'Sınıf' });
  const classWrap = h('label', 'ak-cal-class');
  classWrap.append(h('span', 'ak-cal-class-label', 'Sınıf'), classSelect.node);
  const copyButton = actionButton(app, COPY_WEEK, 'Haftayı kopyala…', { onClick: () => copyDialog() });
  const more = menuButton('Diğer', [
    { label: 'Şablondan doldur…', onClick: () => templateFill(),
      disabled: !editable || !app.can('admin_listTimetableTemplates') },
    { label: 'Sınıf programı (PDF)', onClick: () => classDocument() },
    app.can('admin_getTimetableGrid') ? { label: 'Öğretmen programı (PDF)…', onClick: () => teacherDocument() } : null,
    typeof app.kmRead === 'function' && app.can('admin_copyTimetable')
      ? { label: 'Dönem geçişi kopyası…', onClick: () => rollover() } : null,
    { label: 'Sınıfları düzenle (Öğrenciler › Sınıflar)', onClick: () => app.go?.('students', { view: 'groups' }) },
  ], { variant: 'ghost' });
  const head = h('div', 'ak-cal-head');
  head.append(nav, title, jumpWrap, h('span', 'kit-spacer'), classWrap, copyButton, more);
  node.append(head);

  const content = h('div', 'ak-stack');
  node.append(content);
  const banner = h('div', 'ak-stack ak-cal-banner');
  const gridWrap = h('div', 'ak-tt-gridwrap ak-cal-gridwrap');
  const foot = h('div', 'ak-cal-foot');
  gridWrap.addEventListener('keydown', onGridKey);

  // ------------------------------------------------------------- yükleme
  async function start() {
    if (s.started) return;
    s.started = true;
    content.replaceChildren(skeletonRows(8, 6));
    try {
      const [groups, bells, subjects, teachers] = await Promise.all([
        app.lookups.classGroups(),
        app.lookups.bellSlots(),
        app.lookups.subjects().catch(() => []),
        app.lookups.teachers().catch(() => []),
      ]);
      s.classGroups = groups || [];
      s.bells = bells;
      s.layout = slotLayout(bells?.slots || []);
      s.subjects = subjects || [];
      s.subjectsById = subjectIndex(s.subjects);
      s.teachers = teachers || [];
    } catch (failure) {
      s.started = false;
      content.replaceChildren(errorBox(failure, () => start()));
      return;
    }
    // Sınıf kendiliğinden seçilmez (ilk sınıf ön seçili gelince yanlış sınıfa program girilirdi); tek sınıf açılır.
    if (!s.classGroupId && s.classGroups.length === 1) s.classGroupId = s.classGroups[0].id;
    paintClassOptions();
    await load();
  }

  async function load({ keepFocus = false } = {}) {
    paintTitle();
    if (!s.started) return;
    if (!s.classGroups.length && !s.classGroupId) {
      content.replaceChildren(empty('Sınıf yok',
        'Sınıflar Öğrenciler › Sınıflar bölümünde kurulur (KM sınıfları ve grupları); kurulunca burada listelenir.',
        [button('Sınıflara git', { variant: 'primary', onClick: () => app.go?.('students', { view: 'groups' }) })]));
      return;
    }
    if (!s.classGroupId) {
      content.replaceChildren(empty('Sınıf seçin',
        'Programını görmek ya da girmek istediğiniz sınıfı yukarıdaki “Sınıf” listesinden seçin.'));
      return;
    }
    const mine = ++token;
    if (!s.data) content.replaceChildren(skeletonRows(8, 6));
    try {
      if (s.bellsStale) {
        app.api.invalidate('/v1/admin/bell-slots');
        s.bells = await app.lookups.bellSlots();
        s.layout = slotLayout(s.bells?.slots || []);
        s.bellsStale = false;
      }
      if (s.layout.empty) {
        if (mine !== token) return;
        content.replaceChildren(empty('Zil saatleri tanımlı değil',
          'Takvim zil saatlerine hizalanır. Önce zil saatlerini tanımlayın ya da KM Zil Sistemi’nden çekin.',
          [button('Zil saatlerine git', { variant: 'primary', onClick: () => openBells?.() })]));
        return;
      }
      const data = await app.api.get(weekPath());
      if (mine !== token) return;
      s.data = data;
      if (!keepFocus) s.focus = null;
      content.replaceChildren(banner, gridWrap, foot);
      paint();
    } catch (failure) {
      if (mine !== token) return;
      s.data = null;
      content.replaceChildren(errorBox(failure, () => load()));
    }
  }

  const weekPath = (week = s.week) => `/v1/admin/class-groups/${s.classGroupId}/timetable/weeks/${week}`;

  function paintTitle() {
    titleText.textContent = weekLabel(s.week);
    titleRel.textContent = weekRelation(s.week);
    titleRel.classList.toggle('on', titleRel.textContent === 'Bu hafta');
    thisWeek.disabled = s.week === weekStart(todayIst());
    jump.set(s.week);
  }

  function paintClassOptions() {
    const list = s.classGroups.map((item) => ({ value: item.id, label: item.name }));
    if (s.classGroupId && !list.some((item) => item.value === s.classGroupId)) {
      list.push({ value: s.classGroupId, label: s.data?.classGroup?.name || 'Seçili sınıf' });
    }
    if (!list.length) list.push({ value: '', label: 'Sınıf yok' });
    else if (!s.classGroupId) list.unshift({ value: '', label: 'Sınıf seçin' });
    classSelect.options(list);
    classSelect.set(s.classGroupId);
  }

  // ------------------------------------------------------------- veri yardımcıları
  const days = () => s.data?.days || [];
  const dayOf = (dayKey) => days().find((item) => item.dayOfWeek === dayKey) || null;
  const dateOf = (dayKey) => dayOf(dayKey)?.date || addDays(s.week, Math.max(0, DAY_KEYS.indexOf(dayKey)));
  const weekEditable = () => editable && Boolean(s.data?.editable);
  const className = () => s.data?.classGroup?.name
    || s.classGroups.find((item) => item.id === s.classGroupId)?.name || 'Sınıf';
  const classGrade = () => s.classGroups.find((item) => item.id === s.classGroupId)?.gradeLevel ?? null;

  function entryMap(list) {
    const map = new Map();
    for (const entry of list || []) map.set(cellKey(entry.dayOfWeek, entry.bellSlotId), entry);
    return map;
  }

  function conflictMap() {
    const map = new Map();
    const entries = s.data?.entries || [];
    for (const item of s.data?.conflicts || []) {
      const entry = entries[item.entryIndex];
      const key = entry ? cellKey(entry.dayOfWeek, entry.bellSlotId) : cellKey(item.dayOfWeek, item.bellSlotId);
      map.set(key, [...(map.get(key) || []), item]);
    }
    return map;
  }

  /** Hücre yazılabilir mi; değilse nedeni. */
  function lockReason(dayKey) {
    if (!editable) return 'Programı değiştirme yetkiniz yok.';
    if (!s.data?.editable) return 'Geçmiş hafta — salt okunur.';
    const day = dayOf(dayKey);
    if (day?.past) return 'Geçmiş gün — değiştirilemez (yoklama ve ders kaydı korunur).';
    if (day?.holiday) return `${day.holiday.title} — ders yok.`;
    return '';
  }

  const teacherName = (entry) => entry?.teacher?.displayName
    || s.teachers.find((item) => item.id === entry?.teacher?.id)?.displayName || '';
  const subjectName = (entry, short = false) => {
    const subject = s.subjectsById.get(entry?.subject?.id) || entry?.subject;
    if (!subject) return '';
    return short ? (subject.shortName || subject.name) : subject.name;
  };

  // ---------------------------------------------------------------- çizim
  function paint() {
    paintTitle();
    paintBanner();
    paintGrid();
    paintFoot();
  }

  function paintBanner() {
    banner.replaceChildren();
    const data = s.data;
    if (!data) return;
    if (!editable) {
      banner.append(alertBox('Programı değiştirme yetkiniz yok; takvim salt okunur.', 'info'));
    } else if (!data.editable) {
      banner.append(alertBox('Geçmiş hafta — salt okunur. Yoklaması alınmış dersler korunur; değiştirmek için '
        + 'bu haftaya ya da ileriki bir haftaya geçin.', 'info'));
    }
    if (weekEditable() && !(data.entries || []).length) {
      const box = h('div', 'ak-cal-empty');
      box.append(h('b', '', 'Bu hafta için program yok.'),
        h('span', 'ak-muted', data.previousWeekEntryCount
          ? `Geçen hafta ${num(data.previousWeekEntryCount)} ders vardı.` : 'Hücrelere tıklayarak dersleri girin.'));
      const tools = h('div', 'ak-inline');
      if (data.previousWeekEntryCount && app.can(COPY_WEEK)) {
        tools.append(button('Geçen haftayı kopyala', { variant: 'primary', onClick: () => copyPrevious() }));
      }
      if (app.can('admin_listTimetableTemplates')) {
        tools.append(button('Şablondan doldur', { onClick: () => templateFill() }));
      }
      tools.append(button('Boş başla', { variant: 'ghost', onClick: () => {
        box.remove();
        focusFirst();
      } }));
      box.append(tools);
      banner.append(box);
    }
    if ((data.conflicts || []).length) {
      const box = h('div', 'ak-tt-notice bad');
      box.append(h('b', '', `${num(data.conflicts.length)} çakışma — öğretmen ya da derslik aynı saatte başka sınıfta.`));
      const list = h('ul', 'ak-tt-issues');
      for (const item of data.conflicts) list.append(h('li', '', conflictText(item, s.layout)));
      box.append(list);
      banner.append(box);
    }
    if (s.bells?.source === 'km' && s.bells.available === false) {
      banner.append(alertBox('KM Zil Sistemi’ne şu an ulaşılamıyor; son eşitlenen zil saatleri kullanılıyor'
        + `${s.bells.syncedAt ? ` (${fmtDateTime(s.bells.syncedAt)})` : ''}.`, 'warn'));
    }
    banner.hidden = !banner.childElementCount;
  }

  function paintGrid() {
    const { days: dayKeys, rows } = s.layout;
    const hadFocus = gridWrap.contains(document.activeElement);
    const own = entryMap(s.data?.entries);
    const shared = entryMap(s.data?.sharedEntries);
    const conflicts = conflictMap();
    const grid = h('div', 'ak-tt-grid ak-cal-grid');
    grid.style.gridTemplateColumns = `104px repeat(${dayKeys.length}, minmax(112px, 1fr))`;
    grid.setAttribute('role', 'grid');
    grid.setAttribute('aria-label', `${className()} — ${weekLabel(s.week)} ders programı`);
    grid.append(h('div', 'ak-tt-corner'));
    for (const dayKey of dayKeys) {
      const day = dayOf(dayKey);
      const cell = h('div', `ak-tt-dayhead ak-cal-dayhead${day?.past ? ' ak-cal-is-past' : ''}`
        + `${day?.holiday ? ' ak-cal-is-holiday' : ''}${day?.date === todayIst() ? ' ak-cal-is-today' : ''}`);
      cell.append(h('span', '', dayHead(dayKey, dateOf(dayKey))));
      if (day?.holiday) cell.append(h('small', 'ak-cal-holiday', day.holiday.title));
      grid.append(cell);
    }
    const stop = s.focus || firstKey();
    for (const row of rows) {
      const rowHead = h('div', `ak-tt-rowhead${row.isBreak ? ' ak-tt-is-break' : ''}`);
      rowHead.append(h('b', '', row.label), h('small', '', slotTime(row.ref)));
      grid.append(rowHead);
      for (const dayKey of dayKeys) grid.append(cellNode(dayKey, row, { own, shared, conflicts, stop }));
    }
    gridWrap.replaceChildren(grid);
    if (hadFocus && s.focus) focusCell(s.focus);
  }

  function firstKey() {
    for (const dayKey of s.layout.days) {
      if (lockReason(dayKey)) continue;
      const first = s.layout.lessonsOf(dayKey)[0];
      if (first) return cellKey(dayKey, first.slot.id);
    }
    return null;
  }

  function cellNode(dayKey, row, { own, shared, conflicts, stop }) {
    const slot = row.cells[dayKey];
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
    const key = cellKey(dayKey, slot.id);
    const entry = own.get(key) || null;
    const common = entry ? null : shared.get(key) || null;
    const shown = entry || common;
    const locked = lockReason(dayKey);
    const day = dayOf(dayKey);
    const issues = conflicts.get(key) || [];
    const classes = ['ak-tt-cell', 'ak-cal-cell'];
    if (shown) classes.push('ak-tt-is-filled');
    if (common) classes.push('ak-cal-is-shared');
    if (issues.length) classes.push('ak-tt-is-conflict');
    if (entry?.status === 'orphaned') classes.push('ak-tt-is-orphan');
    if (locked) classes.push('ak-cal-is-locked');
    if (day?.holiday) classes.push('ak-cal-is-holiday');
    if (s.focus === key) classes.push('on');
    const cell = h('button', classes.join(' '));
    cell.type = 'button';
    cell.dataset.key = key;
    cell.tabIndex = key === stop ? 0 : -1;
    if (shown) {
      cell.style.setProperty('--ak-tt-hue', String(subjectHue(shown.subject?.id)));
      cell.append(h('b', 'ak-tt-subject', subjectName(shown, true)));
      const teacher = teacherName(shown);
      cell.append(h('span', `ak-tt-teacher${teacher ? '' : ' ak-cal-missing'}`, teacher || 'Öğretmen yok'));
      if (shown.room) cell.append(h('span', 'ak-tt-room', shown.room));
      const others = (shown.classGroups || []).filter((group) => group.id !== s.classGroupId).map((group) => group.name);
      if (common) cell.append(h('span', 'ak-tt-classes', `ortak: ${shown.ownerClassGroup?.name || others.join(', ')}`));
      else if (others.length) cell.append(h('span', 'ak-tt-classes', `+ ${others.join(', ')}`));
      if (entry?.repeat === 'this_week') cell.append(h('span', 'ak-cal-tag', 'yalnız bu hafta'));
    } else if (day?.holiday) {
      cell.append(h('span', 'ak-cal-off', 'Tatil — ders yok'));
    } else if (!locked) {
      cell.append(h('span', 'ak-tt-add', '+'));
    }
    if (slotTime(slot) !== slotTime(row.ref)) cell.append(h('span', 'ak-tt-time', slotTime(slot)));
    if (issues.length) cell.append(h('span', 'ak-tt-flag', 'Çakışma'));
    const what = shown ? `${subjectName(shown)}${teacherName(shown) ? `, ${teacherName(shown)}` : ''}` : 'boş';
    cell.setAttribute('aria-label', `${DAY_LABEL[dayKey]} ${fmtDate(dateOf(dayKey))} ${row.label} ${slotTime(slot)}: ${what}`
      + `${entry?.repeat === 'this_week' ? ', yalnız bu hafta' : ''}${locked ? `. ${locked}` : ''}`);
    if (locked) cell.setAttribute('aria-disabled', 'true');
    cell.title = [
      ...issues.map((item) => conflictText(item, s.layout)),
      common ? `${shown.ownerClassGroup?.name || 'Başka sınıf'} programında kayıtlı ortak ders; orada düzenlenir.` : '',
      locked,
    ].filter(Boolean).join('\n');
    cell.addEventListener('click', () => activate(key));
    cell.addEventListener('focus', () => { s.focus = key; });
    if (entry && !locked) {
      cell.draggable = true;
      cell.addEventListener('dragstart', (event) => {
        event.dataTransfer?.setData('text/plain', key);
        if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copyMove';
        cell.classList.add('ak-cal-dragging');
      });
      cell.addEventListener('dragend', () => cell.classList.remove('ak-cal-dragging'));
    }
    if (!locked && !common) {
      cell.addEventListener('dragover', (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = event.ctrlKey || event.altKey ? 'copy' : 'move';
        cell.classList.add('ak-cal-over');
      });
      cell.addEventListener('dragleave', () => cell.classList.remove('ak-cal-over'));
      cell.addEventListener('drop', (event) => {
        event.preventDefault();
        cell.classList.remove('ak-cal-over');
        const from = event.dataTransfer?.getData('text/plain');
        if (from && from !== key) moveCell(from, key, { copy: event.ctrlKey || event.altKey || event.metaKey });
      });
    }
    return cell;
  }

  function paintFoot() {
    foot.replaceChildren();
    const counts = new Map();
    for (const entry of s.data?.entries || []) {
      const id = entry.subject?.id;
      if (id) counts.set(id, (counts.get(id) || 0) + 1);
    }
    const summary = h('div', 'ak-tt-summary');
    if (counts.size) {
      summary.append(h('span', 'ak-tt-summary-title', `Bu hafta ${num([...counts.values()].reduce((a, b) => a + b, 0))} ders`));
      for (const [subjectId, count] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
        const chip = h('span', 'ak-tt-chip', `${subjectName({ subject: { id: subjectId } }) || subjectId} · ${num(count)}`);
        chip.style.setProperty('--ak-tt-hue', String(subjectHue(subjectId)));
        summary.append(chip);
      }
    }
    const hint = h('p', 'ak-cal-hint ak-muted', weekEditable()
      ? 'Hücreye tıklayın (ya da Enter) · sürükleyerek taşıyın, Ctrl ile kopyalayın · Delete kaldırır · '
        + 'Ctrl+C / Ctrl+V · PageUp / PageDown hafta değiştirir.'
      : 'PageUp / PageDown hafta değiştirir.');
    const by = s.data?.updatedAt
      ? h('span', 'ak-cal-meta', `Son değişiklik ${fmtDateTime(s.data.updatedAt)}${s.data.updatedBy ? `, ${s.data.updatedBy}` : ''}`)
      : null;
    foot.append(summary, hint, by || '');
  }

  function focusCell(key) {
    const escape = globalThis.CSS?.escape || ((value) => String(value).replace(/"/g, '\\"'));
    gridWrap.querySelector(`[data-key="${escape(key)}"]`)?.focus();
  }

  function focusFirst() {
    const key = firstKey();
    if (key) {
      s.focus = key;
      paintGrid();
      focusCell(key);
    }
  }

  function placeOf(key) {
    const at = String(key).indexOf('|');
    const dayKey = key.slice(0, at);
    const slotId = key.slice(at + 1);
    const place = s.layout.place(dayKey, slotId);
    return place && place.slot.kind === 'lesson' ? { dayKey, slotId, ...place } : null;
  }

  // ---------------------------------------------------------- klavye
  function onGridKey(event) {
    const target = event.target.closest?.('.ak-cal-cell');
    if (event.key === 'PageUp' || event.key === 'PageDown') {
      event.preventDefault();
      goWeek(addDays(s.week, event.key === 'PageUp' ? -7 : 7), { keepFocus: true });
      return;
    }
    if (!target) return;
    const key = target.dataset.key;
    const place = placeOf(key);
    if (!place) return;
    const mod = event.ctrlKey || event.metaKey;
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      const dayKeys = s.layout.days;
      let dayIndex = dayKeys.indexOf(place.dayKey);
      let rowIndex = place.row.index;
      const at = (dayKey, index) => {
        const slot = s.layout.rows[index]?.cells[dayKey];
        return slot && slot.kind === 'lesson' ? cellKey(dayKey, slot.id) : null;
      };
      let found = null;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        const step = event.key === 'ArrowLeft' ? -1 : 1;
        for (dayIndex += step; dayIndex >= 0 && dayIndex < dayKeys.length && !found; dayIndex += step) {
          found = at(dayKeys[dayIndex], rowIndex);
        }
      } else {
        const step = event.key === 'ArrowUp' ? -1 : 1;
        for (rowIndex += step; rowIndex >= 0 && rowIndex < s.layout.rows.length && !found; rowIndex += step) {
          found = at(place.dayKey, rowIndex);
        }
      }
      if (found) {
        s.focus = found;
        for (const item of gridWrap.querySelectorAll('.ak-cal-cell')) {
          item.tabIndex = item.dataset.key === found ? 0 : -1;
          item.classList.toggle('on', item.dataset.key === found);
        }
        focusCell(found);
      }
      return;
    }
    if ((event.key === 'Delete' || event.key === 'Backspace') && !mod) {
      event.preventDefault();
      removeCell(key);
      return;
    }
    if (mod && (event.key === 'c' || event.key === 'C')) {
      const entry = entryMap(s.data?.entries).get(key);
      if (entry) {
        event.preventDefault();
        s.clip = entry;
        app.toast(`${subjectName(entry)} kopyalandı — başka hücrede Ctrl+V ile yapıştırın.`, 'info');
      }
      return;
    }
    if (mod && (event.key === 'v' || event.key === 'V')) {
      if (s.clip) {
        event.preventDefault();
        pasteCell(key);
      }
      return;
    }
    // Harf yazınca düzenleyici açılır (hızlı giriş).
    if (!mod && !event.altKey && event.key.length === 1 && /\S/.test(event.key)) {
      event.preventDefault();
      activate(key, { typed: event.key });
    }
  }

  // ---------------------------------------------------------- yazma
  async function saveCells(cells, scope) {
    try {
      const next = await app.api.put(`${weekPath()}/cells`, { scope, expectedVersion: s.data.version, cells });
      s.data = next;
      paint();
      return next;
    } catch (failure) {
      if (failure instanceof AkademiError && failure.code === 'version_conflict') {
        await load({ keepFocus: true });
        throw new AkademiError({ code: failure.code, status: failure.status,
          message: 'Program bu arada başka biri tarafından değiştirildi; güncel hali yüklendi. Kontrol edip yeniden kaydedin.' });
      }
      if (failure instanceof AkademiError && failure.code === 'timetable_conflict'
        && Array.isArray(failure.details) && failure.details.length) {
        throw new AkademiError({ code: failure.code, status: failure.status,
          message: `Çakışma var, kaydedilmedi. ${failure.details.map((item) => conflictText(item, s.layout)).join(' ')}` });
      }
      throw failure;
    }
  }

  async function run(fn, success) {
    if (s.busy) return null;
    s.busy = true;
    try {
      const result = await fn();
      if (success) app.toast(success, 'good');
      return result;
    } catch (failure) {
      app.toast(problem(failure, 'Program kaydedilemedi.'), 'bad');
      return null;
    } finally {
      s.busy = false;
    }
  }

  function activate(key, { typed = '' } = {}) {
    s.focus = key;
    const place = placeOf(key);
    if (!place) return;
    const own = entryMap(s.data?.entries).get(key);
    const common = own ? null : entryMap(s.data?.sharedEntries).get(key);
    const locked = lockReason(place.dayKey);
    if (common) {
      app.toast(`${subjectName(common)} ortak dersi ${common.ownerClassGroup?.name || 'başka sınıfın'} programında; `
        + 'oradan düzenlenir.', 'info');
      return;
    }
    if (locked) {
      if (s.data) app.toast(locked, 'info');
      return;
    }
    editCell(place, own || null, { typed });
  }

  function editCell(place, entry, { typed = '' } = {}) {
    const { dayKey, slot, row } = place;
    const key = cellKey(dayKey, slot.id);
    dialogSeq += 1;
    const titleLine = `${DAY_LABEL[dayKey]} ${fmtDate(dateOf(dayKey)).slice(0, 5)} · ${row.label} (${slotTime(slot)})`;
    const { overlay, card } = dialogShell(app, titleLine, `${className()} · ${weekLabel(s.week)}`);
    const entries = s.data?.entries || [];
    const field = (label, control, { required = false } = {}) => {
      const wrap = h('label', 'kit-field ak-cal-field');
      const text = h('span', 'kit-field-label', label);
      if (required) text.append(h('span', 'req', '*'));
      wrap.append(text, control);
      return wrap;
    };
    const subject = subjectSelect(s.subjects, entry?.subject?.id || '', { gradeLevel: classGrade(),
      extra: entry?.subject, empty: 'Ders seçin', label: 'Ders' });
    const frequent = frequentSubjects(entries).filter((id) => s.subjectsById.has(id));
    if (frequent.length) {
      const group = h('optgroup');
      group.label = 'Bu sınıfta';
      for (const id of frequent.slice(0, 8)) {
        const option = h('option', '', s.subjectsById.get(id).name);
        option.value = id;
        group.append(option);
      }
      subject.insertBefore(group, subject.children[1] || null);
      subject.value = entry?.subject?.id || '';
    }
    const teacher = selectBox(teacherOptions(s.teachers, entry?.teacher, 'Öğretmen seçin'), entry?.teacher?.id || '',
      () => { error.textContent = ''; }, { label: 'Öğretmen' });
    const room = h('input', 'kit-input');
    room.type = 'text';
    room.maxLength = 40;
    room.placeholder = 'ör. 204';
    room.value = entry?.room || '';
    room.setAttribute('aria-label', 'Derslik');
    const rooms = [...new Set(entries.map((item) => item.room).filter(Boolean))];
    if (rooms.length) {
      const list = h('datalist');
      list.id = `ak-cal-rooms-${dialogSeq}`;
      for (const value of rooms) {
        const option = h('option');
        option.value = value;
        list.append(option);
      }
      room.setAttribute('list', list.id);
      card.append(list);
    }
    subject.addEventListener('change', () => {
      error.textContent = '';
      const hint = suggestionFor(entries, subject.value, s.memory);
      if (hint && !teacher.value) teacher.set(hint.teacherId);
      if (hint?.room && !room.value) room.value = hint.room;
      loadPrograms();
    });

    // Ayrıntılar: birlikte ders yapan sınıflar + müfredat (isteğe bağlı; kapalı gelir).
    const extras = new Set((entry?.classGroups || []).map((group) => group.id).filter((id) => id !== s.classGroupId));
    const details = h('details', 'ak-cal-details');
    details.open = Boolean(extras.size || entry?.program);
    details.append(h('summary', '', 'Birlikte ders yapan sınıflar ve müfredat (isteğe bağlı)'));
    const chips = h('div', 'ak-chips');
    const addClass = h('select', 'kit-select');
    addClass.setAttribute('aria-label', 'Birlikte ders yapan sınıf ekle');
    const paintClasses = () => {
      chips.replaceChildren(h('span', 'kit-chip on', className()));
      const names = new Map([...s.classGroups, ...(entry?.classGroups || [])].map((item) => [item.id, item.name]));
      for (const id of extras) {
        const chip = h('span', 'kit-chip on ak-tt-classchip');
        const x = h('button', 'ak-tt-classchip-x', '×');
        x.type = 'button';
        x.setAttribute('aria-label', `${names.get(id) || 'Sınıf'} sınıfını dersten çıkar`);
        x.addEventListener('click', () => { extras.delete(id); paintClasses(); });
        chip.append(h('span', '', names.get(id) || 'Sınıf'), x);
        chips.append(chip);
      }
      addClass.replaceChildren();
      const blank = h('option', '', extras.size >= 11 ? 'En çok 12 sınıf' : '+ Sınıf ekle (birlikte ders)');
      blank.value = '';
      addClass.append(blank);
      for (const item of s.classGroups) {
        if (item.id === s.classGroupId || extras.has(item.id)) continue;
        const option = h('option', '', item.name);
        option.value = item.id;
        addClass.append(option);
      }
      addClass.value = '';
      addClass.disabled = extras.size >= 11;
    };
    addClass.addEventListener('change', () => {
      if (addClass.value) extras.add(addClass.value);
      paintClasses();
    });
    paintClasses();
    const program = h('select', 'kit-select');
    program.setAttribute('aria-label', 'Müfredat (isteğe bağlı)');
    let programs = [];
    async function loadPrograms() {
      const keep = program.value || entry?.program?.id || '';
      if (!subject.value) {
        fillProgramSelect(program, [], { placeholder: 'Önce ders seçin' });
        program.disabled = true;
        return;
      }
      try {
        const { items } = await fetchPrograms(app, { subjectId: subject.value });
        programs = [...items];
        if (entry?.program && entry.subject?.id === subject.value && !programs.some((item) => item.id === entry.program.id)) {
          programs.push(entry.program);
        }
        fillProgramSelect(program, programs, { placeholder: 'Müfredat yok', recommendedFirst: false });
        program.value = programs.some((item) => item.id === keep) ? keep : '';
        program.disabled = false;
      } catch {
        fillProgramSelect(program, [], { placeholder: 'Müfredat listesi alınamadı' });
      }
    }
    details.append(field('Sınıflar', h('div', 'ak-stack ak-tt-classpick')), field('Müfredat', program));
    details.querySelector('.ak-tt-classpick').append(chips, addClass);
    loadPrograms();

    const scope = scopeRadios(`ak-cal-scope-${dialogSeq}`);
    const form = h('div', 'ak-cal-form');
    form.append(field('Ders', subject, { required: true }), field('Öğretmen', teacher.node, { required: true }),
      field('Derslik', room), details, h('div', 'ak-cal-scope-title', 'Hangi haftalar?'), scope.node);
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(form, error);

    let release = () => {};
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      focusCell(key);
      return value;
    };
    const save = button('Kaydet', { variant: 'primary', onClick: () => submit() });
    const remove = entry ? button('Dersi kaldır', { variant: 'danger', onClick: () => submit({ clear: true }) }) : null;
    async function submit({ clear = false } = {}) {
      error.textContent = '';
      if (!clear && !subject.value) { error.textContent = 'Ders seçin.'; subject.focus(); return; }
      if (!clear && !teacher.value) { error.textContent = 'Öğretmen seçin.'; teacher.node.focus(); return; }
      const cell = clear ? clearCell({ dayOfWeek: dayKey, bellSlotId: slot.id }) : {
        dayOfWeek: dayKey,
        bellSlotId: slot.id,
        subjectId: subject.value,
        teacherId: teacher.value,
        room: room.value.trim() || null,
      };
      if (!clear && extras.size) cell.classGroupIds = [s.classGroupId, ...extras];
      if (!clear && program.value) cell.programId = program.value;
      const chosen = scope.value;
      save.disabled = true;
      if (remove) remove.disabled = true;
      save.textContent = 'Kaydediliyor…';
      try {
        await saveCells([cell], chosen);
        if (!clear) s.memory.set(cell.subjectId, { teacherId: cell.teacherId, room: cell.room || '' });
        close(true);
        app.toast(clear ? `Ders kaldırıldı — ${SCOPE_DONE[chosen]}.` : `Kaydedildi — ${SCOPE_DONE[chosen]}.`, 'good');
      } catch (failure) {
        save.disabled = false;
        if (remove) remove.disabled = false;
        save.textContent = 'Kaydet';
        error.textContent = problem(failure, 'Kaydedilemedi.');
      }
    }
    const onKey = (event) => {
      if (event.key === 'Escape') { event.stopPropagation(); close(null); return; }
      if (event.key === 'Enter' && !event.shiftKey && event.target?.tagName !== 'BUTTON'
        && event.target?.tagName !== 'SUMMARY' && card.contains(event.target)) {
        event.preventDefault();
        submit();
      }
    };
    const actions = h('div', 'kit-dialog-actions');
    if (remove) actions.append(remove, h('span', 'kit-spacer'));
    actions.append(button('Vazgeç', { onClick: () => close(null) }), save);
    card.append(actions);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    if (typed) {
      const wanted = typed.toLocaleLowerCase('tr');
      const match = [...subject.querySelectorAll('option')].find((option) => option.value
        && option.textContent.toLocaleLowerCase('tr').startsWith(wanted));
      if (match) {
        subject.value = match.value;
        subject.dispatchEvent(new Event('change'));
      }
    }
    subject.focus();
  }

  async function removeCell(key) {
    const place = placeOf(key);
    const entry = entryMap(s.data?.entries).get(key);
    if (!place || !entry) return;
    const locked = lockReason(place.dayKey);
    if (locked) { app.toast(locked, 'info'); return; }
    const scope = await askScope(app, { title: `${subjectName(entry)} dersini kaldır`, verb: 'Kaldır',
      description: `${DAY_LABEL[place.dayKey]} ${place.row.label} (${slotTime(place.slot)}) · ${className()}` });
    if (!scope) return;
    await run(() => saveCells([clearCell({ dayOfWeek: place.dayKey, bellSlotId: place.slot.id })], scope),
      `Ders kaldırıldı — ${SCOPE_DONE[scope]}.`);
    focusCell(key);
  }

  async function moveCell(fromKey, toKey, { copy = false } = {}) {
    const from = placeOf(fromKey);
    const to = placeOf(toKey);
    const entry = entryMap(s.data?.entries).get(fromKey);
    if (!from || !to || !entry) return;
    const locked = lockReason(to.dayKey) || (copy ? '' : lockReason(from.dayKey));
    if (locked) { app.toast(locked, 'info'); return; }
    const taken = entryMap(s.data?.entries).get(toKey);
    const verb = copy ? 'Kopyala' : 'Taşı';
    const scope = await askScope(app, { verb,
      title: `${subjectName(entry)}: ${DAY_LABEL[from.dayKey]} ${from.row.label} → ${DAY_LABEL[to.dayKey]} ${to.row.label}`,
      description: taken ? `Hedefteki ${subjectName(taken)} dersinin yerine geçer.` : '' });
    if (!scope) return;
    const cells = [cellOf(entry, { dayOfWeek: to.dayKey, bellSlotId: to.slot.id }, s.classGroupId)];
    if (!copy) cells.unshift(clearCell({ dayOfWeek: from.dayKey, bellSlotId: from.slot.id }));
    s.focus = toKey;
    await run(() => saveCells(cells, scope), `${copy ? 'Kopyalandı' : 'Taşındı'} — ${SCOPE_DONE[scope]}.`);
    focusCell(toKey);
  }

  async function pasteCell(key) {
    const place = placeOf(key);
    if (!place || !s.clip) return;
    const locked = lockReason(place.dayKey);
    if (locked) { app.toast(locked, 'info'); return; }
    const scope = await askScope(app, { verb: 'Yapıştır',
      title: `${subjectName(s.clip)} → ${DAY_LABEL[place.dayKey]} ${place.row.label}` });
    if (!scope) return;
    await run(() => saveCells([cellOf(s.clip, { dayOfWeek: place.dayKey, bellSlotId: place.slot.id },
      s.classGroupId)], scope), `Yapıştırıldı — ${SCOPE_DONE[scope]}.`);
    focusCell(key);
  }

  // ---------------------------------------------------------- kopya / şablon
  async function copyPrevious() {
    const source = addDays(s.week, -7);
    const result = await run(() => app.api.post(
      `/v1/admin/class-groups/${s.classGroupId}/timetable/weeks/${source}/copy`,
      { targetWeekStarts: [s.week], expectedVersion: s.data?.version ?? null },
    ));
    if (!result) return;
    app.toast(result.results?.[0]?.status === 'conflict'
      ? 'Geçen hafta kopyalanamadı: öğretmen ya da derslik çakışması var.'
      : 'Geçen haftanın programı bu haftaya kopyalandı.', result.results?.[0]?.status === 'conflict' ? 'bad' : 'good');
    await load({ keepFocus: true });
  }

  async function copyDialog() {
    if (!s.data) { app.toast('Önce bir sınıf seçin.', 'warn'); return; }
    if (!(s.data.entries || []).length) { app.toast('Bu hafta boş; kopyalanacak ders yok.', 'warn'); return; }
    let term = null;
    try {
      const terms = await app.lookups.terms();
      term = (terms || []).find((item) => item.startsOn <= s.week && item.endsOn >= s.week) || null;
    } catch { /* dönem yoksa "dönem sonuna kadar" gösterilmez */ }
    const today = weekStart(todayIst());
    const options = [];
    for (let i = 1; i <= COPY_WEEKS_SHOWN; i += 1) {
      const monday = addDays(s.week, i * 7);
      if (monday < today) continue;
      if (term && monday > term.endsOn) break;
      options.push({ value: monday, label: shortWeekLabel(monday) });
    }
    const result = await formDialog(app, {
      title: `${weekLabel(s.week)} haftasını kopyala`,
      description: `${className()} sınıfının bu haftası (${num(s.data.entries.length)} ders) seçilen haftalara aynen yazılır; `
        + 'o haftalardaki eski program bununla değişir.',
      fields: [
        { key: 'weeks', label: 'Hangi haftalara?', type: 'custom', render: (draft, set) => {
          const wrap = h('div', 'ak-stack ak-cal-copy');
          const chips = h('div', 'ak-chips');
          const chosen = new Set();
          for (const option of options) {
            const chip = h('button', 'kit-chip', option.label);
            chip.type = 'button';
            chip.setAttribute('aria-pressed', 'false');
            chip.addEventListener('click', () => {
              if (chosen.has(option.value)) chosen.delete(option.value); else chosen.add(option.value);
              chip.classList.toggle('on', chosen.has(option.value));
              chip.setAttribute('aria-pressed', chosen.has(option.value) ? 'true' : 'false');
              set({ ...(draft.weeks || {}), list: [...chosen] });
            });
            chips.append(chip);
          }
          wrap.append(chips);
          if (term) {
            const label = h('label', 'ak-tt-check');
            const box = h('input', 'kit-check');
            box.type = 'checkbox';
            box.addEventListener('change', () => {
              set({ ...(draft.weeks || {}), through: box.checked ? term.endsOn : null });
              chips.classList.toggle('ak-tt-off', box.checked);
            });
            label.append(box, h('span', '', `Dönem sonuna kadar her hafta (${term.name}, ${fmtDate(term.endsOn)})`));
            wrap.append(label);
          }
          set({ list: [], through: null });
          return wrap;
        },
        validate: (value) => (value?.through || value?.list?.length ? '' : 'En az bir hafta seçin.') },
      ],
      submitLabel: 'Kopyala',
      onSubmit: (draft) => app.api.post(`${weekPath()}/copy`, {
        targetWeekStarts: draft.weeks.through ? [] : draft.weeks.list,
        throughDate: draft.weeks.through || null,
        expectedVersion: s.data.version,
      }),
    });
    if (!result || result === true) return;
    const bad = (result.results || []).some((item) => item.status === 'conflict');
    app.toast(copySummary(result.results), bad ? 'warn' : 'good');
    await load({ keepFocus: true });
  }

  async function templateFill() {
    if (!s.data) { app.toast('Önce bir sınıf seçin.', 'warn'); return; }
    let templates;
    try {
      const data = await app.api.get('/v1/admin/timetable-templates');
      templates = Array.isArray(data?.items) ? data.items : (Array.isArray(data) ? data : []);
    } catch (failure) {
      app.toast(problem(failure, 'Şablonlar alınamadı.'), 'bad');
      return;
    }
    if (!templates.length) {
      app.toast('Henüz program şablonu yok. “Diğer” › Program şablonları’ndan oluşturabilirsiniz.', 'info');
      return;
    }
    let scopeControl = null;
    const result = await formDialog(app, {
      title: 'Şablondan doldur',
      description: `${className()} · ${weekLabel(s.week)}. Şablondaki dersler hücrelere yazılır; şablonda olmayan `
        + 'hücreler değişmez.',
      fields: [
        { key: 'templateId', label: 'Şablon', type: 'select', required: true,
          options: [{ value: '', label: 'Şablon seçin' },
            ...templates.map((item) => ({ value: item.id, label: `${item.name} (${num((item.entries || []).length)} ders)` }))] },
        { key: 'scope', label: 'Hangi haftalar?', type: 'custom', render: () => {
          scopeControl = scopeRadios(`ak-cal-tpl-${dialogSeq += 1}`);
          return scopeControl.node;
        } },
      ],
      submitLabel: 'Doldur',
      onSubmit: async (draft) => {
        const template = templates.find((item) => item.id === draft.templateId);
        const { cells, skipped } = templateCells(template?.entries, s.layout, s.classGroupId);
        if (!cells.length) throw new Error('Şablondaki derslerin hiçbiri bu zil saatlerine uymuyor.');
        const scope = scopeControl?.value || 'this_week';
        await saveCells(cells, scope);
        return { count: cells.length, skipped, scope };
      },
    });
    if (!result || result === true) return;
    app.toast(`${num(result.count)} ders yazıldı — ${SCOPE_DONE[result.scope]}.`
      + `${result.skipped ? ` ${num(result.skipped)} satır zil saatlerine uymadığı için atlandı.` : ''}`, 'good');
  }

  // ---------------------------------------------------------- belgeler
  function classDocument() {
    if (!s.data) { app.toast('Önce bir sınıf seçin.', 'warn'); return; }
    runDocument(app, async () => classTimetableDoc({ classGroup: s.data.classGroup, bellSlots: s.bells?.slots || [],
      entries: s.data.entries || [], validFrom: s.data.weekStart, validTo: s.data.weekEnd }),
    { title: 'Sınıf ders programı' });
  }

  async function teacherDocument() {
    const picked = await formDialog(app, {
      title: 'Öğretmen programı (PDF)',
      description: `${weekLabel(s.week)} haftası, bütün sınıflar.`,
      fields: [{ key: 'teacherId', label: 'Öğretmen', type: 'select', required: true,
        options: [{ value: '', label: 'Öğretmen seçin' },
          ...s.teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))] }],
      submitLabel: 'Önizle',
      onSubmit: (draft) => draft.teacherId,
    });
    if (!picked || picked === true) return;
    const teacher = s.teachers.find((item) => item.id === picked);
    runDocument(app, async () => {
      const grid = await app.api.get('/v1/admin/timetable/grid', { date: s.week });
      return teacherTimetableDoc({ teacher: { id: teacher.id, displayName: teacher.displayName },
        bellSlots: grid?.bellSlots || [], groups: grid?.groups || [], date: s.week });
    }, { title: 'Öğretmen ders programı' });
  }

  async function rollover() {
    const result = await rolloverCopyDialog(app, { layout: s.layout });
    if (result) await load({ keepFocus: true });
  }

  // ---------------------------------------------------------- hafta / sınıf
  function goWeek(monday, { keepFocus = false } = {}) {
    if (!monday || monday === s.week) return;
    s.week = monday;
    paintTitle();
    load({ keepFocus });
    if (keepFocus && s.focus) setTimeout(() => focusCell(s.focus), 0);
  }

  function pickClass(id) {
    if (!id || id === s.classGroupId) {
      classSelect.set(s.classGroupId);
      return;
    }
    s.classGroupId = id;
    s.data = null;
    s.focus = null;
    classSelect.set(id);
    if (!s.started) { start(); return; }
    paintClassOptions();
    load();
  }

  return {
    node,
    reload: () => start(),
    /** Derin bağlantı: bu sınıfın takvimini aç (isteğe bağlı hafta). */
    open(classGroupId, week = null) {
      const moved = Boolean(week) && weekStart(week) !== s.week;
      if (week) s.week = weekStart(week);
      if (classGroupId && classGroupId === s.classGroupId) {
        if (moved && s.started) load();
        return;
      }
      pickClass(classGroupId);
    },
    bellsChanged() {
      s.bellsStale = true;
      if (s.started && s.data) load({ keepFocus: true });
    },
    destroy() {
      token += 1;
      for (const fn of disposers.splice(0)) fn();
    },
  };
}
