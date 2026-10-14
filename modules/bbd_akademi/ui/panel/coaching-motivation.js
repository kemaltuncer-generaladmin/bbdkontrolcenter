// BBD Akademi — Koçluk → Tebrik ve rozetler (F-K19).
//
// · Koç rozetleri (`admin_listCoachBadges`; tanımı yalnız KM: create / update /
//   delete — silinen rozet pasifleşir, verilmiş rozetler öğrencide kalır).
//   Kurallı rozetler (F-S8) değişmez; burada yalnız koçun elle verdiği rozetler.
// · Haftalık kutlama (`admin_listCelebrationCandidates`, uyum ≥ %90): adaylar
//   seçilir ve tek mesajla toplu tebrik gider (`admin_bulkKudos`, en çok 200).

import { button, h } from '../../ui-kit/kit.js';
import { badge, card, hintBox } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, formDialog, runAction, selectBox, staticList, todayIst, toolbar, weekStart,
} from './common.js';
import { BADGE_ICONS, REACTIONS, addDaysIso, pct, weekLabel } from './coaching-logic.js';
import { coachOptions, setEnabled, studentCell } from './coaching-shared.js';

const BADGES = '/v1/admin/coaching/badges';

export function tab(app) {
  const node = h('div', 'ak-stack');
  if (app.can('admin_listCelebrationCandidates')) node.append(celebrations(app));
  if (app.can('admin_listCoachBadges')) node.append(badges(app));
  return { node, shown() {} };
}

function badges(app) {
  const body = h('div', 'ak-stack');
  const list = staticList({
    load: () => app.api.getCached(BADGES),
    columns: [
      { key: 'icon', label: 'Simge', width: '70px', cell: (row) => BADGE_ICONS[row.icon]?.[1] || '🏅' },
      { key: 'name', label: 'Rozet', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.name), h('small', '', row.description || ''));
        return cell;
      } },
      { key: 'awarded', label: 'Verildi', width: '100px', align: 'num', cell: (row) => String(row.awardedCount ?? 0) },
      { key: 'active', label: 'Durum', width: '100px', cell: (row) => (row.active === false ? badge('Pasif', 'dim') : badge('Etkin', 'good')) },
      { key: 'act', label: '', width: '200px', cell: (row) => {
        const cell = h('span', 'ak-row-actions');
        cell.append(actionButton(app, 'admin_updateCoachBadge', 'Düzenle', { variant: 'ghost', onClick: () => edit(row) }),
          actionButton(app, 'admin_deleteCoachBadge', 'Kaldır', { variant: 'ghost', onClick: () => remove(row) }));
        return cell;
      } },
    ],
    empty: { title: 'Koç rozeti yok', text: '"Rozet tanımla" ile koçların öğrencilere verebileceği rozetleri ekleyin.' },
  });
  const refresh = () => { app.api.invalidate(BADGES); list.reload(); };
  async function edit(row = null) {
    const result = await formDialog(app, {
      title: row ? 'Rozeti düzenle' : 'Rozet tanımla',
      description: 'Koç rozeti verirken gerekçe yazar; öğrenci rozeti ve gerekçeyi görür.',
      fields: [
        { key: 'name', label: 'Ad', required: true, maxLength: 60,
          validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter.' : null) },
        { key: 'description', label: 'Açıklama (öğrenci görür)', type: 'textarea', maxLength: 300, wide: true },
        { key: 'icon', label: 'Simge', type: 'select', required: true,
          options: Object.entries(BADGE_ICONS).map(([value, [label, emoji]]) => ({ value, label: `${emoji} ${label}` })) },
        { key: 'active', label: 'Etkin', type: 'checkbox' },
      ],
      value: { name: row?.name || '', description: row?.description || '', icon: BADGE_ICONS[row?.icon] ? row.icon : 'medal',
        active: row ? row.active !== false : true },
      onSubmit: (draft, { key }) => {
        const body = { name: String(draft.name).trim(), description: String(draft.description || '').trim() || null,
          icon: draft.icon || 'medal', active: Boolean(draft.active) };
        return row ? app.api.put(`${BADGES}/${row.id}`, body) : app.api.post(BADGES, body, { key });
      },
    });
    if (result) { app.toast('Rozet kaydedildi.', 'good'); refresh(); }
  }
  async function remove(row) {
    if (!await confirmAction(app, { title: 'Rozeti kaldır', description: `"${row.name}" pasifleşir; daha önce verilenler `
      + 'öğrencide kalır.', confirmLabel: 'Kaldır', danger: true })) return;
    const done = await runAction(app, () => app.api.del(`${BADGES}/${row.id}`).then(() => true), { success: 'Rozet kaldırıldı.' });
    if (done) refresh();
  }
  body.append(toolbar(actionButton(app, 'admin_createCoachBadge', 'Rozet tanımla', { variant: 'primary',
    onClick: () => edit() })), list.node);
  list.reload();
  return card('Koç rozetleri', body, 'tanımı yalnız KM');
}

function celebrations(app) {
  const body = h('div', 'ak-stack');
  let week = weekStart(todayIst(-7));
  let coachId = '';
  const label = h('b', 'ak-coach-weeklabel', weekLabel(week));
  const move = (days) => { week = addDaysIso(week, days); label.textContent = weekLabel(week); list.reload(); };
  const coachSlot = h('div');
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachSlot.append(selectBox(options, '', (value) => { coachId = value; list.reload(); }, { label: 'Koç' }).node);
  });
  const send = actionButton(app, 'admin_bulkKudos', 'Seçilenleri tebrik et', { variant: 'primary', onClick: () => congratulate() });
  setEnabled(send, false);
  const list = staticList({
    load: () => app.api.get('/v1/admin/coaching/celebrations', { weekStart: week, coachId: coachId || undefined }),
    selectable: true,
    rowKey: (row) => row.student?.id,
    onSelect: (keys) => setEnabled(send, keys.length > 0),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => studentCell(row.student) },
      { key: 'adherence', label: 'Haftalık uyum', width: '140px', align: 'num', cell: (row) => pct(row.adherencePercent) },
      { key: 'done', label: 'Tebrik', width: '160px', cell: (row) => (row.congratulated ? badge('Tebrik edildi', 'good') : badge('Bekliyor', 'info')) },
    ],
    empty: { title: 'Bu hafta aday yok', text: 'Haftalık plan uyumu %90 ve üstü olan öğrenciler burada listelenir.' },
  });
  async function congratulate() {
    const studentIds = list.table.selection();
    if (!studentIds.length) return;
    const result = await formDialog(app, {
      title: `${studentIds.length} öğrenciyi tebrik et`,
      description: 'Her öğrenciye aynı mesaj gider (bildirim).',
      fields: [
        { key: 'text', label: 'Mesaj', type: 'textarea', required: true, maxLength: 280, wide: true },
        { key: 'sticker', label: 'Çıkartma', type: 'select',
          options: [{ value: '', label: 'Yok' }, ...REACTIONS.map((item) => ({ value: item, label: item }))] },
      ],
      value: { text: `Bu hafta planına harika uydun (${weekLabel(week)})! Böyle devam.`, sticker: '🎉' },
      submitLabel: 'Gönder',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/coaching/kudos/bulk', { studentIds: studentIds.slice(0, 200),
        text: String(draft.text).trim().slice(0, 280), sticker: draft.sticker || null }, { key }),
    });
    if (!result) return;
    app.toast(`${result.notifiedCount} öğrenciye tebrik gitti.`, 'good');
    list.reload();
  }
  body.append(toolbar(button('‹ Önceki hafta', { variant: 'ghost', onClick: () => move(-7) }), label,
    button('Sonraki hafta ›', { variant: 'ghost', onClick: () => move(7) }), coachSlot, send),
  hintBox('Video izlemek, seri ya da puan kazandırmaz; tebrikler plan uyumuna göre önerilir.'), list.node);
  list.reload();
  return card('Haftalık kutlama', body, 'uyum ≥ %90');
}
