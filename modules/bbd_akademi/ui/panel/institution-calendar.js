// BBD Akademi — Kurum takvimi (F-A20).
//
// Tatil, etkinlik, veli toplantısı, deneme günü, kayıt dönemi. Etkinlik hedef
// kitlenin ajandasına düşer (öğrenci ve veli, F-S20); isteğe bağlı bağlı
// duyuru (F-A13) ve hatırlatma (`agenda` bildirimi). Yalnız KM yönetir; mobil
// yönetici görür.
//
// TATİL BAĞLAYICIDIR: `holiday` günlerinde yoklama oturumu beklenmez (F-A7),
// plan önerisi ve plan ataması o günlere görev koymaz (F-S16, F-A18); o güne
// düşmüş koç görevleri atamanın tatil kuralına göre kayar ya da atlanır. Etkiyi
// Akademi hesaplar (`affectsAttendance`, `affectsPlanning`); ekran gösterir ve
// formda önceden söyler.
//
// AY GÖRÜNÜMÜ: görünen 6 haftalık ızgaranın TAMAMI tek aralık olarak istenir
// (42 gün; sözleşme sınırı 400) ve ETag önbelleğiyle tutulur: ay ileri-geri
// gezilirken aynı ay yeniden indirilmez, her yazmadan sonra önbellek düşer.
// Liste görünümü imleçlidir (pagedList).
//
// BAĞLI DUYURU yalnız oluştururken açılır; Akademi güncellemede bağlı duyuruyu
// değiştirmez ("ayrı düzenlenir"), bu yüzden düzenlemede mevcut ayar olduğu
// gibi geri gönderilir ve duyuruya Duyurular bölümünden gidilir.
//
// ZİL (sözleşme km-ogrenci-gruplari-ve-zil.md §3.5, §8.5): kurum takvimi zil
// sisteminin geliştirilmiş hâlidir — etkinlik günün zil kipini belirler:
// `normal` (haftalık saatler), `off` (tatil: zil, anons ve çağrı çalmaz; tatil
// türünde varsayılan) ya da KM'de tanımlı adlı bir saat seti; isteğe bağlı
// "dersleri iptal et". Saat setleri KM Zil Sistemi'nden okunur
// (`GET /api/bell/state` → `profiles`; yoksa yalnız normal/tatil). Alan
// sözleşmeye 2d91b96 ile girdi (`bell` + `cancelLessons`); `calendarEventBell` şalteri kapalıyken
// form alanı devre dışı görünür ve gövdeye EKLENMEZ (bell-plan.js → withBell).
//
// Derin bağlantı: `{institutionEventId}`, `{date}` (o günün ayı açılır, gün seçilir).

import { h, loadStyles, longDate } from '../../ui-kit/kit.js';
import { monthCalendar } from '../../ui-kit/calendar.js';
import { dateField, dateRange } from '../../ui-kit/datefield.js';
import { alertBox, card, hintBox, tabBar } from '../../ui-kit/layout.js';
import { AkademiError } from './api.js';
import { bellChoices, bellText, choiceFromBell, defaultBellChoice, withBell } from './bell-plan.js';
import {
  actionButton, addDays, errorBox, fmtDate, fmtDateTime, formDialog, istanbulStamp, kv, loader, multiChips,
  num, openDrawer, optionsOf, pagedList, sectionHead, selectBox, studentPicker, todayIst, toolbar,
} from './common.js';

const KINDS = {
  holiday: 'Tatil', event: 'Etkinlik', parent_meeting: 'Veli toplantısı', exam_day: 'Deneme günü',
  registration_period: 'Kayıt dönemi', other: 'Diğer',
};
/** Ay hücresine sığan kısa ad (rozet metni; renk tek başına anlam taşımaz). */
const SHORT = {
  holiday: 'Tatil', event: 'Etkinlik', parent_meeting: 'Veli', exam_day: 'Deneme',
  registration_period: 'Kayıt', other: 'Diğer',
};
const KIND_ORDER = ['holiday', 'exam_day', 'parent_meeting', 'registration_period', 'event', 'other'];
const RECIPIENTS = { all: 'Veliler ve öğrenciler', guardians: 'Yalnız veliler', students: 'Yalnız öğrenciler' };
const SCOPES = { tenant: 'Bütün kurum', class_groups: 'Sınıflar', student_groups: 'Çalışma grupları',
  students: 'Seçili öğrenciler' };
const REMINDERS = [[0, 'Başlarken'], [60, '1 saat önce'], [180, '3 saat önce'], [1440, '1 gün önce'],
  [2880, '2 gün önce'], [10080, '1 hafta önce'], [20160, '2 hafta önce']];
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const HOLIDAY_RULE = 'Tatil günlerinde yoklama beklenmez; plan önerisi ve plan ataması o günlere görev koymaz, '
  + 'o güne düşen koç görevleri atamanın tatil kuralına göre kayar ya da atlanır.';
const BELL_PROFILES_TTL = 5 * 60_000;

function reminderText(minutes) {
  const known = REMINDERS.find(([value]) => value === minutes);
  if (known) return known[1];
  if (minutes % 1440 === 0) return `${num(minutes / 1440)} gün önce`;
  if (minutes % 60 === 0) return `${num(minutes / 60)} saat önce`;
  return `${num(minutes)} dk önce`;
}

function kindPill(kind) {
  return h('span', `ak-ical-pill ak-ical-k-${KINDS[kind] ? kind : 'other'}`, KINDS[kind] || kind || '—');
}

function dateText(event) {
  if (!event.endDate || event.endDate === event.startDate) return fmtDate(event.startDate);
  return `${fmtDate(event.startDate)} – ${fmtDate(event.endDate)}`;
}

function timeText(event) {
  if (event.allDay !== false) return 'Tüm gün';
  return event.endTime ? `${event.startTime}–${event.endTime}` : (event.startTime || '—');
}

function audienceText(audience, names = {}) {
  if (!audience) return 'Bütün kurum';
  const ids = audience.scope === 'class_groups' ? audience.classGroupIds
    : audience.scope === 'student_groups' ? audience.studentGroupIds
      : audience.scope === 'students' ? audience.studentIds : [];
  const map = audience.scope === 'class_groups' ? names.classes
    : audience.scope === 'student_groups' ? names.groups : null;
  let scope = SCOPES[audience.scope] || audience.scope || '';
  if (ids?.length) {
    const known = map ? ids.map((id) => map.get(id)).filter(Boolean) : [];
    scope = known.length === ids.length && known.length <= 3 ? known.join(', ')
      : `${scope} (${num(ids.length)})`;
  }
  return `${RECIPIENTS[audience.recipients] || ''} · ${scope}`;
}

/** 'YYYY-MM' → görünen 42 günlük ızgaranın ilk ve son günü (Pazartesi başlar). */
function gridRange(monthKey) {
  const first = `${monthKey}-01`;
  const [year, month] = monthKey.split('-').map(Number);
  const lead = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
  const from = addDays(first, -lead);
  return { from, to: addDays(from, 41) };
}

/** Önbellekli tüm sayfalar (ay ızgarası küçük ve sınırlı bir kümedir). */
async function fetchAll(app, path, query, { max = 500 } = {}) {
  const rows = [];
  let cursor;
  do {
    const data = await app.api.getCached(path, { ...query, limit: 100, cursor }); // eslint-disable-line no-await-in-loop
    rows.push(...(data?.items || []));
    cursor = data?.nextCursor || undefined;
  } while (cursor && rows.length < max);
  return rows;
}

function loadNames(app) {
  return Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]).then(([classes, groups]) => ({
    classes, groups,
    names: { classes: new Map(classes.map((item) => [item.id, item.name])),
      groups: new Map(groups.map((item) => [item.id, item.name])) },
  }));
}

/**
 * Zil alanı bağlamı: şalter + KM saat setleri (form açılınca bir kez, 5 dk
 * önbellek). Şalter kapalıyken istek atılmaz. Sonuç `null` = Zil Sistemi'ne
 * ulaşılamadı / izin yok / saat seti desteği yok → yalnız normal/tatil.
 */
function bellContext(app) {
  const enabled = app.feature('calendarEventBell');
  let request = null;
  let list = null;
  let at = 0;
  return {
    enabled,
    /** Son okunan saat setleri (ayrıntıda ad çözmek için; istek atmaz). */
    known: () => list || [],
    profiles() {
      if (!enabled) return Promise.resolve(null);
      if (request && Date.now() - at < BELL_PROFILES_TTL) return request;
      at = Date.now();
      request = app.kmGet('/api/bell/state')
        .then((data) => (Array.isArray(data?.profiles) ? data.profiles : null))
        .catch(() => null)
        .then((found) => {
          list = found;
          if (found === null) request = null;   // bir dahaki form yeniden dener
          return found;
        });
      return request;
    },
  };
}

// =================================================================== bölüm

export function section(app) {
  loadStyles(import.meta.url, 'institution-calendar.css');
  const node = h('div', 'ak-stack ak-ical');
  const filters = { kinds: [], classGroupId: '' };
  let started = false;
  let lookup = null;
  let names = { classes: new Map(), groups: new Map() };
  const disposers = [];

  const ready = () => {
    if (!lookup) lookup = loadNames(app);
    return lookup;
  };
  const bell = bellContext(app);

  const add = actionButton(app, 'admin_createCalendarEvent', 'Etkinlik ekle', { variant: 'primary',
    onClick: () => create(selected || todayIst()) });
  node.append(sectionHead('Kurum takvimi', {
    subtitle: 'Tatil, etkinlik, veli toplantısı, deneme günü ve kayıt dönemi; hedef kitlenin ajandasına düşer.',
    actions: [add],
  }), hintBox(HOLIDAY_RULE));

  const kindChips = multiChips(optionsOf(KINDS), [], (list) => { filters.kinds = list; refresh(); });
  const classSelect = selectBox([{ value: '', label: 'Bütün kurum' }], '', (value) => {
    filters.classGroupId = value; refresh();
  }, { label: 'Sınıf' });
  classSelect.node.title = 'Seçilen sınıfa hedeflenenler (kurum geneli dahil)';
  node.append(toolbar(kindChips.node, h('span', 'kit-spacer'), classSelect.node));

  const tabs = tabBar([{ key: 'month', label: 'Ay' }, { key: 'list', label: 'Liste' }], 'month',
    (key) => showView(key));
  node.append(tabs.node);

  const query = () => ({ kind: filters.kinds.length ? filters.kinds.join(',') : undefined,
    classGroupId: filters.classGroupId || undefined });

  // ------------------------------------------------------------ ay görünümü
  let selected = null;
  let monthEvents = [];
  let byDay = {};
  let token = 0;
  const monthView = h('div', 'ak-ical-month');
  const calendar = monthCalendar({
    selected: null,
    onPick: (iso) => { selected = iso; paintDay(); },
    onMonth: () => loadMonth(),
    renderBadge: (iso, events) => marks(events),
    dayTitle: (iso, events) => (events || []).map((event) => `${KINDS[event.kind] || ''}: ${event.title}`).join(' · '),
    legend: KIND_ORDER.map((kind) => ({ text: `${SHORT[kind]} = ${KINDS[kind].toLocaleLowerCase('tr')}`,
      tone: `ak-ical-t-${kind}` })),
  });
  disposers.push(() => calendar.destroy());
  const calendarCard = card('', calendar.node);
  calendarCard.classList.add('ak-ical-cal');
  const dayPanel = h('div', 'ak-stack ak-ical-day');
  const monthStatus = h('div', 'ak-ical-status');
  const left = h('div', 'ak-stack');
  left.append(calendarCard, monthStatus);
  monthView.append(left, dayPanel);

  function marks(events) {
    if (!events?.length) return null;
    const wrap = h('span', 'ak-ical-marks');
    const kinds = KIND_ORDER.filter((kind) => events.some((event) => event.kind === kind));
    for (const kind of kinds.slice(0, 2)) {
      const count = events.filter((event) => event.kind === kind).length;
      wrap.append(h('span', `ak-ical-mark ak-ical-k-${kind}`, count > 1 ? `${SHORT[kind]} ${count}` : SHORT[kind]));
    }
    if (kinds.length > 2) wrap.append(h('span', 'ak-ical-mark ak-ical-more', `+${kinds.length - 2}`));
    return wrap;
  }

  async function loadMonth() {
    const mine = ++token;
    const span = gridRange(calendar.month());
    monthStatus.replaceChildren(h('span', 'ak-muted', 'Takvim yükleniyor…'));
    try {
      const [items, loaded] = await Promise.all([
        fetchAll(app, '/v1/admin/calendar/events', { from: span.from, to: span.to, ...query() }),
        ready(),
      ]);
      if (mine !== token) return;
      names = loaded.names;
      monthEvents = items;
      byDay = {};
      const holidays = {};
      for (const event of items) {
        const first = event.startDate > span.from ? event.startDate : span.from;
        const lastDay = event.endDate && event.endDate > event.startDate ? event.endDate : event.startDate;
        const last = lastDay < span.to ? lastDay : span.to;
        for (let day = first; day <= last; day = addDays(day, 1)) {
          (byDay[day] ||= []).push(event);
          if (event.kind === 'holiday') holidays[day] = `Tatil: ${event.title}`;
        }
      }
      calendar.update({ days: byDay, holidays });
      const inMonth = items.filter((event) => (event.endDate || event.startDate) >= `${calendar.month()}-01`
        && event.startDate <= `${calendar.month()}-31`).length;
      monthStatus.replaceChildren(h('span', 'ak-muted', inMonth ? `Bu ay ${num(inMonth)} kayıt.` : 'Bu ay kayıt yok.'));
      paintDay();
    } catch (failure) {
      if (mine !== token) return;
      monthStatus.replaceChildren(errorBox(failure, () => loadMonth()));
    }
  }

  function eventRow(event) {
    const row = h('button', 'ak-ical-row');
    row.type = 'button';
    const head = h('div', 'ak-inline');
    head.append(kindPill(event.kind), h('b', '', event.title));
    const meta = [dateText(event), timeText(event), event.location, audienceText(event.audience, names)]
      .filter(Boolean).join(' · ');
    row.append(head, h('small', 'ak-muted', meta));
    row.addEventListener('click', () => openEvent(event.id));
    return row;
  }

  function paintDay() {
    dayPanel.replaceChildren();
    if (selected) {
      const events = byDay[selected] || [];
      const head = h('div', 'ak-inline');
      head.append(h('h3', 'ak-ical-day-title', longDate(selected)), h('span', 'kit-spacer'),
        actionButton(app, 'admin_createCalendarEvent', 'Bu güne ekle', { onClick: () => create(selected) }));
      dayPanel.append(head);
      if (events.some((event) => event.kind === 'holiday')) dayPanel.append(alertBox(HOLIDAY_RULE, 'info'));
      if (!events.length) dayPanel.append(h('div', 'ak-muted', 'Bu gün için kayıt yok.'));
      for (const event of events) dayPanel.append(eventRow(event));
      const back = h('button', 'ak-ical-link', 'Ayın bütün kayıtları');
      back.type = 'button';
      back.addEventListener('click', () => { selected = null; calendar.update({ selected: null }); paintDay(); });
      dayPanel.append(back);
      return;
    }
    const month = calendar.month();
    const events = monthEvents.filter((event) => (event.endDate || event.startDate) >= `${month}-01`
      && event.startDate <= `${month}-31`);
    dayPanel.append(h('h3', 'ak-ical-day-title', 'Bu ayın kayıtları'));
    if (!events.length) {
      dayPanel.append(h('div', 'ak-muted', 'Bu ay için kayıt yok. Bir güne tıklayıp “Bu güne ekle” ile başlayın.'));
    }
    for (const event of events) dayPanel.append(eventRow(event));
  }

  // ---------------------------------------------------------- liste görünümü
  const listView = h('div', 'ak-stack');
  listView.hidden = true;
  const span = { from: todayIst(), to: todayIst(89) };
  const range = dateRange({ start: span.from, end: span.to, label: 'Tarih', presets: [], onChange: (value) => {
    span.from = value.start || span.from;
    span.to = value.end || span.to;
    reloadList();
  } });
  disposers.push(() => range.destroy());
  const rangeNote = h('span', 'ak-muted', 'En çok 400 günlük aralık.');
  listView.append(toolbar(range.node, rangeNote));
  const list = pagedList({
    app,
    path: '/v1/admin/calendar/events',
    query: () => ({ from: span.from, to: span.to, ...query() }),
    columns: [
      { key: 'date', label: 'Tarih', width: '180px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', dateText(row)), h('small', '', timeText(row)));
        return cell;
      } },
      { key: 'kind', label: 'Tür', width: '130px', cell: (row) => kindPill(row.kind) },
      { key: 'title', label: 'Başlık', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.title), h('small', '', row.location || ''));
        return cell;
      } },
      { key: 'audience', label: 'Hedef kitle', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', audienceText(row.audience, names)),
          h('small', '', `${num(row.studentReach)} öğrenci`));
        return cell;
      } },
      { key: 'links', label: 'Bağlantı', width: '130px', cell: (row) => [row.announcementId ? 'Duyuru' : '',
        row.examId ? 'Deneme' : '', (row.reminders || []).length ? `${num(row.reminders.length)} hatırlatma` : '']
        .filter(Boolean).join(' · ') || '—' },
    ],
    onRow: (row) => openEvent(row.id),
    empty: { title: 'Bu aralıkta kayıt yok', text: 'Tarih aralığını genişletin ya da tür süzgecini temizleyin.' },
  });
  listView.append(list.node);
  disposers.push(() => list.destroy());

  function reloadList() {
    const days = Math.round((Date.parse(span.to) - Date.parse(span.from)) / 86_400_000);
    if (days < 0) { app.toast('Bitiş günü başlangıçtan önce olamaz.', 'warn'); return; }
    if (days > 400) { app.toast('Aralık en çok 400 gün olabilir.', 'warn'); return; }
    ready().then((loaded) => { names = loaded.names; list.reload(); });
  }

  node.append(monthView, listView);
  let listLoaded = false;

  function showView(key) {
    monthView.hidden = key !== 'month';
    listView.hidden = key !== 'list';
    if (key === 'list' && !listLoaded) { listLoaded = true; reloadList(); }
  }

  function refresh() {
    loadMonth();
    if (listLoaded) reloadList();
  }

  /** Yazmadan sonra: önbellek düşer, iki görünüm tazelenir. */
  function changed() {
    app.api.invalidate('/v1/admin/calendar/events');
    refresh();
  }

  async function create(day) {
    const saved = await editEvent(app, null, { day, ready, bell });
    if (saved) {
      app.toast(saved.announcementId ? `“${saved.title}” eklendi; bağlı duyuru da oluşturuldu.`
        : `“${saved.title}” takvime eklendi.`, 'good');
      changed();
    }
  }

  function openEvent(id) {
    eventDrawer(app, id, { ready, bell, onChange: changed });
  }

  return {
    node,
    shown(payload) {
      if (!started) {
        started = true;
        ready().then((loaded) => {
          classSelect.options([{ value: '', label: 'Bütün kurum' },
            ...loaded.classes.map((item) => ({ value: item.id, label: item.name }))]);
        });
      }
      if (payload?.date && /^\d{4}-\d{2}-\d{2}$/.test(payload.date)) {
        selected = payload.date;
        calendar.setMonthFrom(payload.date);
        calendar.update({ selected: payload.date });
        tabs.select('month');
        loadMonth();
      } else if (token === 0) {
        selected = todayIst();
        calendar.update({ selected });
        loadMonth();
      }
      if (payload?.institutionEventId) openEvent(payload.institutionEventId);
    },
    destroy() {
      for (const fn of disposers) fn();
    },
  };
}

// ================================================================ ayrıntı

function eventDrawer(app, id, { ready, bell, onChange }) {
  const panel = openDrawer(app, { title: 'Takvim kaydı', subtitle: 'Kurum takvimi' });
  const view = loader(() => Promise.all([app.api.get(`/v1/admin/calendar/events/${id}`), ready()]),
    ([event, loaded]) => {
      panel.setTitle(event.title);
      const nodes = [];
      if (event.kind === 'holiday' || event.affectsAttendance || event.affectsPlanning) {
        nodes.push(alertBox(HOLIDAY_RULE, 'info'));
      }
      const tools = h('div', 'ak-inline');
      tools.append(
        actionButton(app, 'admin_updateCalendarEvent', 'Düzenle', { variant: 'primary', onClick: async () => {
          const saved = await editEvent(app, event, { ready, bell });
          if (saved) { app.toast('Takvim kaydı güncellendi.', 'good'); onChange?.(); view.reload(); }
        } }),
        actionButton(app, 'admin_deleteCalendarEvent', 'Sil', { variant: 'ghost', onClick: async () => {
          if (await removeEvent(app, event)) { panel.close(); onChange?.(); }
        } }),
      );
      if (event.announcementId) {
        const go = h('button', 'kit-btn kit-btn-ghost', 'Bağlı duyuruyu aç');
        go.type = 'button';
        go.addEventListener('click', () => { panel.close(); app.go('announcements', { announcementId: event.announcementId }); });
        tools.append(go);
      }
      if (event.examId) {
        const go = h('button', 'kit-btn kit-btn-ghost', 'Denemeyi aç');
        go.type = 'button';
        go.addEventListener('click', () => { panel.close(); app.go('exams', { examId: event.examId }); });
        tools.append(go);
      }
      nodes.push(tools);
      const weekday = (iso) => longDate(iso);
      nodes.push(card('', kv([
        ['Tür', kindPill(event.kind)],
        ['Tarih', event.endDate && event.endDate !== event.startDate
          ? `${weekday(event.startDate)} – ${weekday(event.endDate)}` : weekday(event.startDate)],
        ['Saat', timeText(event)],
        ['Yer', event.location || '—'],
        ['Hedef kitle', audienceText(event.audience, loaded.names)],
        ['Ulaşılan öğrenci', num(event.studentReach)],
        event.bell ? ['Zil', bellText(event.bell, bell?.known(), event.cancelLessons)] : null,
        ['Yoklama', event.affectsAttendance ? 'Bu günlerde yoklama beklenmez' : 'Etkilenmez'],
        ['Çalışma planı', event.affectsPlanning ? 'Plan önerisi ve plan ataması bu günlere görev koymaz' : 'Etkilenmez'],
        ['Hatırlatma', (event.reminders || []).length
          ? event.reminders.map((item) => reminderText(item.minutesBefore)).join(', ') : 'Yok'],
        ['Bildirim', event.notify === false ? 'Kapalı' : 'Açık (oluşturma, tarih değişikliği, iptal)'],
        ['Bağlı duyuru', event.announcementId
          ? `Var${event.announce?.pinned ? ' · sabit' : ''}${event.announce?.publishAt ? ` · ${fmtDateTime(event.announce.publishAt)}` : ''}`
          : 'Yok'],
        ['Oluşturan', `${event.createdBy || '—'} · ${fmtDateTime(event.createdAt)}`],
        ['Sürüm', `${num(event.version)}${event.updatedAt ? ` · ${fmtDateTime(event.updatedAt)}` : ''}`],
      ])));
      if (event.description) nodes.push(card('Açıklama', h('div', 'ak-ical-desc', event.description)));
      return nodes;
    });
  panel.body.append(view.node);
  view.reload();
}

async function removeEvent(app, event) {
  const result = await formDialog(app, {
    title: `“${event.title}” silinsin mi?`,
    description: `Kayıt hedef kitlenin ajandasından kalkar.${event.kind === 'holiday'
      ? ' Tatil silinince bu günler yeniden olağan gün sayılır: yoklama beklenir, plan önerisi görev koyabilir.' : ''}`,
    value: { notify: event.notify !== false },
    fields: [{ key: 'notify', label: 'Hedef kitleye iptal bildirimi gönder', type: 'checkbox', wide: true }],
    submitLabel: 'Kaydı sil',
    danger: true,
    onSubmit: async (draft) => {
      await app.api.del(`/v1/admin/calendar/events/${event.id}`, { query: { notify: Boolean(draft.notify) } });
      return true;
    },
  });
  if (result) app.toast('Takvim kaydı silindi.', 'good');
  return Boolean(result);
}

// ======================================================== oluştur / düzenle

/** Etkinlik penceresi. `current` mevcut kayıt ya da null. Sonuç kaydedilen etkinlik. */
async function editEvent(app, current, { day = todayIst(), ready, bell = null } = {}) {
  const bellEnabled = Boolean(bell?.enabled);
  const [{ classes, groups }, profiles] = await Promise.all([ready(), bell ? bell.profiles() : null]);
  const audience = current?.audience || { recipients: 'all', scope: 'tenant' };
  const chosenStudents = new Map();
  // Önceki öğrenci hedefinin adları yükte yok (satır başına istek atılmaz): toplu çip.
  let keptStudents = audience.scope === 'students' ? [...(audience.studentIds || [])] : [];
  let picker = null;
  let publishDay = null;

  const value = current ? {
    kind: current.kind, title: current.title, description: current.description || '',
    startDate: current.startDate, endDate: current.endDate || '', allDay: current.allDay !== false,
    startTime: current.startTime || '', endTime: current.endTime || '', location: current.location || '',
    recipients: audience.recipients || 'all', scope: audience.scope || 'tenant', notify: current.notify !== false,
    classGroupIds: audience.classGroupIds || [], studentGroupIds: audience.studentGroupIds || [],
    examId: current.examId || '',
    reminders: (current.reminders || []).map((item) => String(item.minutesBefore)),
    bell: { choice: choiceFromBell(current.bell, current.kind), cancel: Boolean(current.cancelLessons),
      touched: Boolean(current.bell?.mode) },
  } : {
    kind: 'event', title: '', description: '', startDate: day, endDate: '', allDay: true, startTime: '',
    endTime: '', location: '', recipients: 'all', scope: 'tenant', notify: true, classGroupIds: [],
    studentGroupIds: [], examId: '', reminders: ['1440'],
    announce: { enabled: false, pinned: false, day: '', clock: '09:00' },
    bell: { choice: defaultBellChoice('event'), cancel: false, touched: false },
  };

  const clockRule = (raw, draft) => {
    if (draft.allDay) return null;
    if (!raw) return 'Tüm gün değilse saat yazın.';
    return CLOCK.test(raw) ? null : 'Saati 09:30 gibi yazın.';
  };

  /** Kapsam değişince (formDialog `update`) yeniden çizilen hedef seçici. */
  const targetPicker = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    let shown = null;
    const paint = () => {
      const scope = draft.scope || 'tenant';
      if (scope === shown) return;
      shown = scope;
      picker?.destroy();
      picker = null;
      wrap.replaceChildren();
      if (scope === 'class_groups') {
        const chips = multiChips(classes.map((item) => ({ value: item.id, label: item.name })),
          draft.classGroupIds, (list) => { draft.classGroupIds = list; set(list); });
        wrap.append(classes.length ? chips.node : h('span', 'ak-muted', 'Sınıf tanımlı değil.'));
      } else if (scope === 'student_groups') {
        const chips = multiChips(groups.map((item) => ({ value: item.id, label: item.name })),
          draft.studentGroupIds, (list) => { draft.studentGroupIds = list; set(list); });
        wrap.append(groups.length ? chips.node : h('span', 'ak-muted', 'Çalışma grubu yok.'));
      } else if (scope === 'students') {
        const picked = h('div', 'ak-chips');
        const paintPicked = () => {
          picked.replaceChildren();
          if (keptStudents.length) {
            const chip = h('button', 'kit-chip on', `Önceden seçilen ${num(keptStudents.length)} öğrenci ✕`);
            chip.type = 'button';
            chip.title = 'Hepsini hedeften çıkar';
            chip.addEventListener('click', () => { keptStudents = []; paintPicked(); set([...chosenStudents.keys()]); });
            picked.append(chip);
          }
          for (const [id, name] of chosenStudents) {
            const chip = h('button', 'kit-chip on', `${name} ✕`);
            chip.type = 'button';
            chip.addEventListener('click', () => { chosenStudents.delete(id); paintPicked(); set([...chosenStudents.keys()]); });
            picked.append(chip);
          }
          if (!keptStudents.length && !chosenStudents.size) picked.append(h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
        };
        picker = studentPicker(app, { onPick: (student) => {
          chosenStudents.set(student.id, student.displayName || student.id);
          paintPicked();
          set([...chosenStudents.keys()]);
        } });
        paintPicked();
        wrap.append(picker.node, picked);
      } else {
        wrap.append(h('span', 'ak-muted', 'Bütün kuruma gider (alıcı seçimine göre veliler ve/veya öğrenciler).'));
      }
    };
    paint();
    return { node: wrap, update: paint };
  };

  /** Deneme günü → bağlı deneme (liste yalnız bu tür seçilince istenir). */
  const examPicker = (draft, set) => {
    const wrap = h('div');
    const select = h('select', 'kit-select');
    select.setAttribute('aria-label', 'Bağlı deneme');
    let loaded = false;
    const fill = (options) => {
      select.replaceChildren();
      for (const option of options) {
        const item = h('option', '', option.label);
        item.value = option.value;
        select.append(item);
      }
    };
    select.addEventListener('change', () => set(select.value));
    async function load() {
      loaded = true;
      fill([{ value: '', label: 'Yükleniyor…' }]);
      try {
        const data = await app.api.getCached('/v1/admin/exams', { from: todayIst(-30), limit: 100 });
        const items = data?.items || [];
        const options = [{ value: '', label: 'Bağlı deneme yok' },
          ...items.map((exam) => ({ value: exam.id, label: `${exam.name} · ${fmtDate(exam.date)}` }))];
        if (draft.examId && !items.some((exam) => exam.id === draft.examId)) {
          options.push({ value: draft.examId, label: 'Kayıtlı deneme' });
        }
        fill(options);
        select.value = draft.examId || '';
      } catch {
        loaded = false;
        fill([{ value: draft.examId || '', label: 'Denemeler alınamadı' }]);
      }
    }
    const paint = () => {
      wrap.replaceChildren();
      if (draft.kind !== 'exam_day') {
        wrap.append(h('span', 'ak-muted', 'Tür “Deneme günü” seçilince bir denemeye bağlanabilir.'));
        return;
      }
      wrap.append(select);
      if (!loaded) load();
    };
    paint();
    return { node: wrap, update: paint };
  };

  const reminderField = (draft, set) => {
    const options = REMINDERS.map(([minutes, label]) => ({ value: String(minutes), label }));
    for (const raw of draft.reminders || []) {
      if (!options.some((item) => item.value === raw)) options.push({ value: raw, label: reminderText(Number(raw)) });
    }
    const chips = multiChips(options, draft.reminders || [], (list) => set(list));
    set(chips.value);
    return chips.node;
  };

  /**
   * Zil: kip seçimi + "dersleri iptal et". Kullanıcı seçime dokunmadıkça tür
   * değişince önerilen kip izler (tatil → zil çalmaz). Şalter kapalıyken alan
   * görünür ama devre dışıdır ve gövdeye eklenmez.
   */
  const bellField = (draft, set) => {
    const state = { ...draft.bell };
    const wrap = h('div', 'ak-stack');
    const row = h('div', 'ak-inline');
    const select = h('select', 'kit-select');
    select.setAttribute('aria-label', 'Zil');
    const fill = () => {
      select.replaceChildren();
      for (const option of bellChoices(profiles, state.choice.startsWith('profile:') ? state.choice : '')) {
        const item = h('option', '', option.label);
        item.value = option.value;
        select.append(item);
      }
      select.value = state.choice;
    };
    select.addEventListener('change', () => { state.choice = select.value; state.touched = true; set({ ...state }); });
    const cancelBox = h('label', 'ak-inline ak-ical-check');
    const cancel = h('input', 'kit-check');
    cancel.type = 'checkbox';
    cancel.checked = Boolean(state.cancel);
    cancel.addEventListener('change', () => { state.cancel = cancel.checked; set({ ...state }); });
    cancelBox.append(cancel, h('span', '', 'Dersleri iptal et (o günlerin oturumları toplu iptal)'));
    row.append(select, cancelBox);
    wrap.append(row);
    if (!bellEnabled) {
      select.disabled = true;
      cancel.disabled = true;
      wrap.append(h('span', 'kit-field-hint', 'Akademi bu alanı henüz desteklemiyor; seçim kaydedilmez. '
        + 'O güne kadar tatil ve özel günlerde zil KM Zil Sistemi\'nden yönetilir.'));
    } else if (profiles === null) {
      wrap.append(h('span', 'kit-field-hint', 'KM Zil Sistemi\'nden saat setleri alınamadı; yalnız normal ve tatil '
        + 'seçilebilir. Saat seti eklemek için Zil Sistemi ekranını kullanın.'));
    } else {
      wrap.append(h('span', 'kit-field-hint', 'Tatil: o gün zil, zil sonrası anons ve oturum çağrısı çalmaz. '
        + 'Saat seti: KM Zil Sistemi\'nde tanımlı adlı saatler çalar.'));
    }
    let kind = draft.kind;
    const update = (next) => {
      if (next.kind === kind) return;
      kind = next.kind;
      if (state.touched) return;
      state.choice = defaultBellChoice(kind);
      fill();
      set({ ...state });
    };
    fill();
    set({ ...state });
    return { node: wrap, update };
  };

  const announceField = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    if (current) {
      wrap.append(h('span', 'ak-muted', current.announcementId
        ? 'Bu kaydın bağlı duyurusu var. Akademi güncellemede duyuruyu değiştirmez; duyuruyu Duyurular bölümünden düzenleyin.'
        : 'Bağlı duyuru yalnız kayıt oluşturulurken açılır. Gerekirse Duyurular bölümünden ayrı duyuru yazın.'));
      return wrap;
    }
    const state = { ...draft.announce };
    const row = h('div', 'ak-inline');
    const toggle = (label, key) => {
      const box = h('label', 'ak-inline ak-ical-check');
      const input = h('input', 'kit-check');
      input.type = 'checkbox';
      input.checked = Boolean(state[key]);
      input.addEventListener('change', () => { state[key] = input.checked; paint(); set({ ...state }); });
      box.append(input, h('span', '', label));
      return box;
    };
    row.append(toggle('Bağlı duyuru da oluştur', 'enabled'), toggle('Duyuruyu sabitle', 'pinned'));
    const pinRow = row.lastChild;
    const timing = h('div', 'ak-inline');
    publishDay = dateField({ value: '', label: 'Duyuru yayın günü', onChange: (iso) => {
      state.day = iso;
      set({ ...state });
    } });
    const clock = h('input', 'kit-input ak-ical-clock');
    clock.type = 'text';
    clock.maxLength = 5;
    clock.placeholder = 'SS:DD';
    clock.value = state.clock || '09:00';
    clock.setAttribute('aria-label', 'Duyuru saati');
    clock.addEventListener('input', () => { state.clock = clock.value.trim(); set({ ...state }); });
    timing.append(h('span', 'ak-muted', 'Yayın (boş = hemen):'), publishDay.node, clock);
    const paint = () => {
      pinRow.hidden = !state.enabled;
      timing.hidden = !state.enabled;
    };
    wrap.append(row, timing, h('span', 'kit-field-hint',
      'Duyuru bu kaydın bilgileriyle oluşturulur. Yayın günü boşsa hemen yayımlanır.'));
    paint();
    set({ ...state });
    return wrap;
  };

  try {
    return await formDialog(app, {
      title: current ? 'Takvim kaydını düzenle' : 'Takvime ekle',
      description: current ? 'Tarih ya da saat değişirse ve bildirim açıksa hedef kitleye “güncellendi” bildirimi gider.' : '',
      wide: true,
      value,
      fields: [
        { key: 'kind', label: 'Tür', type: 'select', required: true, options: optionsOf(KINDS),
          hint: 'Tatil: yoklama beklenmez, plan önerisi ve plan ataması o günlere görev koymaz.' },
        { key: 'title', label: 'Başlık', required: true, maxLength: 120,
          validate: (raw) => (String(raw || '').trim().length < 2 ? 'En az 2 karakter yazın.' : null) },
        { key: 'startDate', label: 'Başlangıç günü', type: 'date', required: true },
        { key: 'endDate', label: 'Bitiş günü (isteğe bağlı)', type: 'date' },
        { key: 'allDay', label: 'Tüm gün', type: 'checkbox' },
        { key: 'location', label: 'Yer (isteğe bağlı)', maxLength: 120 },
        { key: 'startTime', label: 'Başlangıç saati', maxLength: 5, placeholder: 'SS:DD', validate: clockRule,
          hint: 'Tüm gün kayıtta boş kalır.' },
        { key: 'endTime', label: 'Bitiş saati (isteğe bağlı)', maxLength: 5, placeholder: 'SS:DD',
          validate: (raw, draft) => (draft.allDay || !raw || CLOCK.test(raw) ? null : 'Saati 10:30 gibi yazın.') },
        { key: 'description', label: 'Açıklama (isteğe bağlı)', type: 'textarea', maxLength: 2000, wide: true },
        { key: 'recipients', label: 'Kime', type: 'select', options: optionsOf(RECIPIENTS) },
        { key: 'scope', label: 'Kapsam', type: 'select', options: optionsOf(SCOPES) },
        { key: 'notify', label: 'Hedef kitleye ajanda bildirimi gönder', type: 'checkbox', wide: true },
        { key: 'targets', label: 'Hedef', type: 'custom', render: targetPicker, wide: true,
          validate: (_raw, draft) => {
            if (draft.scope === 'class_groups' && !(draft.classGroupIds || []).length) return 'En az bir sınıf seçin.';
            if (draft.scope === 'student_groups' && !(draft.studentGroupIds || []).length) return 'En az bir grup seçin.';
            if (draft.scope === 'students' && !chosenStudents.size && !keptStudents.length) return 'En az bir öğrenci seçin.';
            return null;
          } },
        { key: 'examId', label: 'Bağlı deneme', type: 'custom', render: examPicker, wide: true },
        { key: 'reminders', label: 'Hatırlatma', type: 'custom', render: reminderField, wide: true,
          hint: 'Ajanda bildirimi olarak gider; tüm gün kayıtta günün 09:00\'una göre hesaplanır.' },
        { key: 'bell', label: 'Zil', type: 'custom', render: bellField, wide: true },
        { key: 'announce', label: 'Duyuru', type: 'custom', render: announceField, wide: true },
      ],
      submitLabel: current ? 'Kaydet' : 'Takvime ekle',
      onSubmit: (draft, { key }) => {
        const fail = (message) => { throw new AkademiError({ code: 'validation_error', message }); };
        if (draft.endDate && draft.endDate < draft.startDate) fail('Bitiş günü başlangıç gününden önce olamaz.');
        const allDay = Boolean(draft.allDay);
        if (!allDay && draft.endTime && draft.endTime <= draft.startTime) fail('Bitiş saati başlangıçtan sonra olmalı.');
        const scope = draft.scope || 'tenant';
        let announce;
        if (current) {
          announce = current.announce || { enabled: false, pinned: false, publishAt: null };
        } else {
          const state = draft.announce || {};
          let publishAt = null;
          if (state.enabled && state.day) {
            const clock = state.clock || '09:00';
            if (!CLOCK.test(clock)) fail('Duyuru saatini 09:00 gibi yazın.');
            publishAt = istanbulStamp(state.day, clock);
          }
          announce = { enabled: Boolean(state.enabled), pinned: Boolean(state.enabled && state.pinned), publishAt };
        }
        const body = withBell({
          kind: draft.kind,
          title: String(draft.title || '').trim(),
          description: String(draft.description || '').trim() || null,
          startDate: draft.startDate,
          endDate: draft.endDate && draft.endDate !== draft.startDate ? draft.endDate : null,
          allDay,
          startTime: allDay ? null : draft.startTime || null,
          endTime: allDay ? null : draft.endTime || null,
          location: String(draft.location || '').trim() || null,
          audience: {
            recipients: draft.recipients || 'all',
            scope,
            classGroupIds: scope === 'class_groups' ? draft.classGroupIds || [] : [],
            studentGroupIds: scope === 'student_groups' ? draft.studentGroupIds || [] : [],
            studentIds: scope === 'students' ? [...new Set([...keptStudents, ...chosenStudents.keys()])] : [],
          },
          examId: draft.kind === 'exam_day' ? draft.examId || null : null,
          reminders: [...new Set((draft.reminders || []).map(Number))].sort((a, b) => a - b)
            .map((minutesBefore) => ({ minutesBefore })),
          announce,
          notify: Boolean(draft.notify),
        }, { enabled: bellEnabled, choice: draft.bell?.choice, cancelLessons: draft.bell?.cancel });
        return current ? app.api.put(`/v1/admin/calendar/events/${current.id}`, body)
          : app.api.post('/v1/admin/calendar/events', body, { key });
      },
    });
  } finally {
    picker?.destroy();
    publishDay?.destroy();
  }
}
