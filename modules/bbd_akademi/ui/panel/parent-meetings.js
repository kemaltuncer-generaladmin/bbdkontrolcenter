// BBD Akademi — Bugün → Veli toplantısı (Akademi README §14 madde 90, İ8; F-P11, F-A14).
//
// Dönemde bir-iki kez yapılan toplu veli görüşmesinin RANDEVU TAKVİMİ. Öğretmen / koç uygulamadan kendi saatlerini
// açar; KM (kurumun bütün öğretmenleri, `parent_meetings.manage:tenant`) burada:
//   · saatleri gün gün görür (`admin_listParentMeetingSlots`; öğretmen süzgeci, en çok 62 gün),
//   · bir öğretmen adına gün + saat aralığını eşit randevu saatlerine böler (`admin_createParentMeetingSlots`;
//     süre + ara, yer, görüşme şekli, "bütün velilere açık"),
//   · telefonla arayan veli adına randevu verir (`admin_bookParentMeetingSlot`; öğrenci seçici),
//   · saati kapatır (`admin_cancelParentMeetingSlot`: randevusuz saat silinir, randevulu saatin görüşmesi iptal
//     edilir ve veliye bildirilir),
//   · günün listesini basar (`admin_getParentMeetingListPdf`; öğretmen başına saat, öğrenci, veli, not, imza).
// Randevu Akademi'de PLANLANMIŞ GÖRÜŞME TALEBİDİR: Talepler → Görüşme'de, velinin Görüşmelerim listesinde ve
// öğrencinin ajandasında aynı kayıt görünür; veli iptal ederse saat kendiliğinden boşalır.
//
// Derin bağlantı: `{date: 'YYYY-MM-DD'}` o günden başlar, `{staffId}` öğretmeni süzer.

import { h } from '../../ui-kit/kit.js';
import { alertBox, badge } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { previewAkademiPdf } from './documents.js';
import {
  actionButton, addDays, clockInput, confirmAction, dateInput, empty, fmtDate, formDialog, loader, num, openDrawer,
  personName, problem, reasonDialog, runAction, sectionHead, selectBox, studentPicker, todayIst, toolbar,
} from './common.js';

export const MODES = { in_person: 'Yüz yüze', online: 'Online', phone: 'Telefon' };
export const SLOT_MINUTES = [5, 10, 15, 20, 30];
export const BREAK_MINUTES = [0, 2, 5, 10];
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const DAY_NAMES = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];

/** "10:00" → dakika. */
function minutesOf(clock) {
  const [hour, minute] = String(clock || '').split(':').map(Number);
  return (hour || 0) * 60 + (minute || 0);
}

/** Açılacak saat sayısı (Akademi `plan_slots` kuralı; geçmiş ve çakışanlar sunucuda ayrıca atlanır). */
export function plannedCount(start, end, slot, gap = 0) {
  const a = minutesOf(start);
  const b = minutesOf(end);
  const step = Number(slot || 0);
  if (!CLOCK.test(start || '') || !CLOCK.test(end || '') || b <= a || step <= 0) return 0;
  let count = 0;
  for (let cur = a; cur + step <= b; cur += step + Number(gap || 0)) count += 1;
  return count;
}

/** İstanbul saatine göre "HH:MM". */
export function clockOf(stamp) {
  return new Date(stamp).toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul', hour: '2-digit', minute: '2-digit' });
}

/** İstanbul günü "YYYY-MM-DD". */
export function dayOf(stamp) {
  return new Date(stamp).toLocaleDateString('en-CA', { timeZone: 'Europe/Istanbul' });
}

/** Saatleri güne göre gruplar (sıra korunur): `[[gün, saatler[]], …]`. */
export function byDay(items) {
  const map = new Map();
  for (const item of items || []) {
    const key = dayOf(item.startsAt);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return [...map.entries()];
}

/** Durum rozeti: Boş / Dolu / Yapıldı. */
export function slotState(slot) {
  if (!slot.booking) return ['Boş', ''];
  return slot.booking.status === 'done' ? ['Yapıldı', 'good'] : ['Dolu', 'info'];
}

function dayTitle(day) {
  const weekday = DAY_NAMES[new Date(`${day}T12:00:00Z`).getUTCDay()];
  return `${fmtDate(day)} ${weekday}`;
}

export function section(app) {
  const node = h('div', 'ak-stack');
  node.append(sectionHead('Veli toplantısı', {
    subtitle: 'Öğretmenlerin veli görüşmesi saatleri: saat aç, veli adına randevu ver, günün listesini yazdır. Veli '
      + 'uygulamadan çocuğunun öğretmeninden boş saati seçer; randevu Talepler → Görüşme\'de de görünür.' }));
  const filter = { from: todayIst(), to: todayIst(29), staffId: '' };
  const from = dateInput(filter.from, (value) => { filter.from = value || todayIst(); view.reload(); }, 'Başlangıç');
  const to = dateInput(filter.to, (value) => { filter.to = value || addDays(filter.from, 29); view.reload(); }, 'Bitiş');
  const staffSlot = h('div');
  let staffOptions = [];
  app.lookups?.teachers?.().then((list) => {
    staffOptions = (list || []).map((item) => ({ value: item.id, label: item.displayName || personName(item) }));
    staffSlot.append(selectBox([{ value: '', label: 'Bütün öğretmenler' }, ...staffOptions], '',
      (value) => { filter.staffId = value; view.reload(); }, { label: 'Öğretmen' }).node);
  }).catch(() => {});
  node.append(toolbar(from.node, to.node, staffSlot, h('span', 'kit-spacer'),
    actionButton(app, 'admin_createParentMeetingSlots', 'Saat aç', { variant: 'primary', onClick: () => openSlots() })));

  const view = loader(() => app.api.get('/v1/admin/parent-meeting-slots', Object.fromEntries(
    Object.entries(filter).filter(([, value]) => value))), (data) => {
    const items = data?.items || [];
    if (!items.length) {
      return empty('Açık randevu saati yok', 'Öğretmenler uygulamadan saat açabilir ya da buradan “Saat aç” ile '
        + 'bir öğretmen adına açılır.');
    }
    const multi = new Set(items.map((item) => item.staff?.id)).size > 1;
    return byDay(items).map(([day, slots]) => dayBlock(day, slots, multi));
  }, { rows: 6, columns: 5 });
  node.append(view.node);

  function dayBlock(day, slots, multi) {
    const booked = slots.filter((slot) => slot.booking).length;
    const block = h('div', 'ak-stack');
    block.append(toolbar(h('b', '', dayTitle(day)), badge(`${num(booked)}/${num(slots.length)} dolu`, booked ? 'info' : ''),
      h('span', 'kit-spacer'),
      actionButton(app, 'admin_getParentMeetingListPdf', 'Listeyi yazdır', { variant: 'ghost',
        onClick: (event) => print(day, event.currentTarget) })));
    block.append(dataTable({
      columns: [
        { key: 'time', label: 'Saat', width: '110px', cell: (slot) => `${clockOf(slot.startsAt)}–${clockOf(slot.endsAt)}` },
        multi ? { key: 'staff', label: 'Öğretmen', width: 'minmax(0, 1fr)', cell: (slot) => personName(slot.staff) } : null,
        { key: 'who', label: 'Öğrenci · veli', width: 'minmax(0, 1.6fr)', cell: (slot) => (slot.booking
          ? h('span', '', `${slot.booking.student?.displayName || ''}${slot.booking.student?.classGroup
            ? ` (${slot.booking.student.classGroup.name})` : ''} · ${slot.booking.bookedBy?.displayName || ''}`)
          : h('span', 'ak-muted', [slot.title, slot.location, MODES[slot.mode],
            slot.audience === 'all' ? 'Bütün velilere açık' : ''].filter(Boolean).join(' · '))) },
        { key: 'note', label: 'Not', width: 'minmax(0, 1fr)', cell: (slot) => slot.booking?.note || '' },
        { key: 'state', label: 'Durum', width: '90px', cell: (slot) => badge(...slotState(slot)) },
        { key: 'actions', label: '', width: '230px', cell: (slot) => slotActions(slot) },
      ].filter(Boolean),
      rows: slots,
      rowKey: (slot) => String(slot.id),
    }).node);
    return block;
  }

  function slotActions(slot) {
    const box = h('div', 'ak-row-actions');
    if (Date.parse(slot.startsAt) <= Date.now()) return box;
    if (!slot.booking) {
      box.append(actionButton(app, 'admin_bookParentMeetingSlot', 'Veli adına randevu', { variant: 'ghost',
        onClick: () => bookFor(slot) }),
      actionButton(app, 'admin_cancelParentMeetingSlot', 'Saati kapat', { variant: 'ghost', onClick: () => closeFree(slot) }));
    } else if (slot.booking.status === 'scheduled') {
      box.append(actionButton(app, 'admin_cancelParentMeetingSlot', 'Randevuyu iptal et', { variant: 'ghost',
        onClick: () => cancelBooked(slot) }));
    }
    return box;
  }

  async function print(day, opener) {
    const query = { date: day };
    if (filter.staffId) query.staffId = filter.staffId;
    const signed = await runAction(app, () => app.api.get('/v1/admin/parent-meeting-slots/pdf', query));
    if (signed?.url) previewAkademiPdf(app, { kind: 'parent_meeting_list', url: signed.url, name: day, opener });
  }

  async function closeFree(slot) {
    const ok = await confirmAction(app, { title: 'Saat kapatılsın mı?', description: 'Boş saat silinir.',
      confirmLabel: 'Saati kapat', danger: true });
    if (!ok) return;
    const result = await runAction(app, () => app.api.post(`/v1/admin/parent-meeting-slots/${slot.id}/cancel`,
      { reason: null }), { success: 'Saat kapatıldı.' });
    if (result) view.reload();
  }

  async function cancelBooked(slot) {
    const reason = await reasonDialog(app, { title: 'Randevuyu iptal et', confirmLabel: 'İptal et',
      description: 'Görüşme iptal edilir, veliye bildirilir ve saat kapanır. Not veliye gider.',
      presets: ['Öğretmen o gün kurumda değil', 'Toplantı ertelendi'] });
    if (!reason) return;
    const result = await runAction(app, () => app.api.post(`/v1/admin/parent-meeting-slots/${slot.id}/cancel`,
      { reason }), { success: 'Randevu iptal edildi.' });
    if (result) view.reload();
  }

  function bookFor(slot) {
    const drawer = openDrawer(app, { title: 'Veli adına randevu',
      subtitle: `${personName(slot.staff)} · ${fmtDate(dayOf(slot.startsAt))} ${clockOf(slot.startsAt)}` });
    const picker = studentPicker(app, { onPick: async (student) => {
      const result = await runAction(app, () => app.api.post(`/v1/admin/parent-meeting-slots/${slot.id}/book`,
        { studentId: student.id, note: null }), { success: 'Randevu verildi.' });
      if (result) {
        drawer.close();
        picker.destroy?.();
        view.reload();
      }
    } });
    drawer.body.replaceChildren(alertBox('Telefonla arayan veli için. Aynı öğretmenden açık randevusu olan öğrenciye '
      + 'ikinci randevu verilmez.', 'info'), picker.node);
    picker.focus?.();
  }

  async function openSlots() {
    if (!staffOptions.length) {
      try {
        staffOptions = ((await app.lookups?.teachers?.()) || [])
          .map((item) => ({ value: item.id, label: item.displayName || personName(item) }));
      } catch (failure) {
        app.toast(problem(failure, 'Öğretmen listesi alınamadı.'), 'bad');
        return;
      }
    }
    const preview = h('small', 'ak-muted');
    const result = await formDialog(app, {
      title: 'Randevu saatleri aç',
      description: 'Gün ve saat aralığı eşit randevu saatlerine bölünür (en çok 60). Öğretmenin aynı saatteki açık '
        + 'saatleri ve geçmiş saatler atlanır.',
      fields: [
        { key: 'staffId', label: 'Öğretmen', type: 'select', required: true,
          options: [{ value: '', label: 'Öğretmen seçin' }, ...staffOptions] },
        { key: 'title', label: 'Başlık', required: true, maxLength: 120 },
        { key: 'date', label: 'Gün', type: 'date', required: true },
        { key: 'startTime', label: 'Başlangıç', type: 'custom', required: true, wide: false,
          render: (draft, set) => clockInput(draft.startTime || '', (value) => set(value), 'Başlangıç').node },
        { key: 'endTime', label: 'Bitiş', type: 'custom', required: true, wide: false,
          render: (draft, set) => clockInput(draft.endTime || '', (value) => set(value), 'Bitiş').node },
        { key: 'slotMinutes', label: 'Randevu süresi', type: 'select', required: true,
          options: SLOT_MINUTES.map((value) => ({ value: String(value), label: `${value} dk` })) },
        { key: 'breakMinutes', label: 'Ara', type: 'select',
          options: BREAK_MINUTES.map((value) => ({ value: String(value), label: value ? `${value} dk` : 'Ara yok' })) },
        { key: 'mode', label: 'Görüşme şekli', type: 'select', options: Object.entries(MODES)
          .map(([value, label]) => ({ value, label })) },
        { key: 'location', label: 'Yer (ör. Oda 3)', maxLength: 80 },
        { key: 'everyone', label: 'Kurumun bütün velilerine açık (müdür, rehberlik)', type: 'checkbox', wide: true },
        { key: 'preview', label: '', type: 'custom', wide: true,
          render: (draft) => { preview.textContent = previewText(draft); return preview; },
          update: (draft) => { preview.textContent = previewText(draft); } },
      ],
      value: { staffId: filter.staffId || '', title: 'Veli toplantısı', date: todayIst(7), startTime: '10:00',
        endTime: '13:00', slotMinutes: '10', breakMinutes: '5', mode: 'in_person', location: '', everyone: false },
      submitLabel: 'Saatleri aç',
      onSubmit: (draft, { key }) => {
        if (!draft.staffId) throw new Error('Öğretmen seçin.');
        if (!CLOCK.test(draft.startTime || '') || !CLOCK.test(draft.endTime || '')) {
          throw new Error('Saatleri SS:DD biçiminde yazın.');
        }
        if (!plannedCount(draft.startTime, draft.endTime, draft.slotMinutes, draft.breakMinutes)) {
          throw new Error('Bu aralıkta randevu saati çıkmıyor.');
        }
        return app.api.post('/v1/admin/parent-meeting-slots', {
          staffId: draft.staffId, title: String(draft.title || '').trim(), date: draft.date,
          startTime: draft.startTime, endTime: draft.endTime, slotMinutes: Number(draft.slotMinutes),
          breakMinutes: Number(draft.breakMinutes || 0), mode: draft.mode || 'in_person',
          location: String(draft.location || '').trim() || null, audience: draft.everyone ? 'all' : 'related',
        }, { key });
      },
    });
    if (!result) return;
    const skipped = Number(result.skippedCount || 0);
    app.toast(skipped ? `${num(result.items?.length || 0)} saat açıldı · ${num(skipped)} saat çakıştığı için atlandı.`
      : `${num(result.items?.length || 0)} saat açıldı.`, 'good');
    view.reload();
  }

  function previewText(draft) {
    const count = plannedCount(draft.startTime, draft.endTime, draft.slotMinutes, draft.breakMinutes);
    return count ? `${num(count)} randevu saati açılacak.` : 'Bu aralıkta randevu saati çıkmıyor.';
  }

  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.date) { filter.from = payload.date; filter.to = addDays(payload.date, 29); }
      if (payload?.staffId) filter.staffId = payload.staffId;
      if (!started || payload?.date || payload?.staffId) { started = true; view.reload(); }
    },
    destroy() { from.destroy?.(); to.destroy?.(); },
  };
}

