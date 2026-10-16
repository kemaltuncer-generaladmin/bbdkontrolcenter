// BBD Akademi — Koçluk → Şablonlar (Akademi README §14 madde 115; kullanıcı 16.10.2026: "koçluk kısmına şablon
// ekleyelim").
//
// Koçun tekrar tekrar yazdığı her şey TEK yerde:
//
//   · Metin şablonları (`admin_listCoachTextTemplates`): görüşme notu (hazır eylem maddeleri + varsayılan
//     görünürlük), soru yanıtı, check-in yorumu, veli görüşmesi, mesaj, tebrik. Hazır (yerleşik) şablonlar silinmez,
//     metni değişmez; kurum yalnız etkinleştirir / pasifleştirir, sıralar ya da kopyalayıp kendi şablonunu yapar.
//     Kurum şablonu ekle / düzenle / sil (`admin_*CoachTextTemplate`). Kullanım sayısı görünür. Koçların kişisel
//     şablonları yalnız sahiplerine görünür (KM'de listelenmez).
//   · Program şablonları (`admin_listStudyPlanTemplates`): haftalık program / plan şablonları (aynı ekran
//     `plan-templates.js`); "Öğrencilere ata" koç görevi olarak önizlemeli toplu atama (`templateAssignDialog`).
//     Haftalık programda "Şablondan ekle" ve "Bu haftayı şablon olarak kaydet" de bu şablonları kullanır.

import { h, loadStyles } from '../../ui-kit/kit.js';
import { badge, chipRow, hintBox, skeletonRows } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, errorBox, fmtDateTime, formDialog, kv, openDrawer, optionsOf, runAction, searchBox,
  selectBox, staticList, toolbar,
} from './common.js';
import { NOTE_VISIBILITY } from './coaching-logic.js';
import { invalidateTemplates } from './shared/text-templates.js';
import {
  ACTION_KINDS, KIND_LABELS, MAX_LENGTH, PLACEHOLDER_LABELS, SCOPE_LABELS, SCOPE_TONES, TEXT_TEMPLATES, TEXTS,
  actionsText, filterTemplates, preview, templateBody,
} from './shared/text-templates-logic.js';

export function tab(app, ctx = {}) {
  loadStyles(import.meta.url, 'shared/text-templates.css');
  const node = h('div', 'ak-stack');
  const views = [
    app.can('admin_listCoachTextTemplates') && { key: 'text', label: 'Metin şablonları' },
    app.can('admin_listStudyPlanTemplates') && { key: 'program', label: 'Program şablonları' },
  ].filter(Boolean);
  let view = views[0]?.key;
  const chips = chipRow(views, view, (key) => { view = key || view; chips.set(view); paint(); });
  const holder = h('div');
  node.append(toolbar(chips.node), holder);
  let text = null;
  let program = null;
  function paint() {
    if (view === 'program') {
      if (!program) {
        const slot = h('div', 'ak-stack');
        slot.append(skeletonRows(5, 4));
        program = { node: slot, destroy: () => {} };
        import('./plan-templates.js').then(async (module) => {
          const instance = module.templatesTab(app, ctx);
          const assign = actionButton(app, 'admin_previewTemplateAssignment', 'Öğrencilere ata (koç görevi)', {
            variant: 'ghost', onClick: () => import('./coaching-week.js').then((week) => week.templateAssignDialog(app)) });
          slot.replaceChildren(hintBox('Haftalık program şablonları: koç haftasında “Şablondan ekle” ile tek öğrenciye, '
            + '“Öğrencilere ata” ile sınıfa / gruba koç görevi olarak yazılır. Bir öğrencinin haftasını “Bu haftayı şablon '
            + 'olarak kaydet” ile buraya ekleyebilirsiniz.'), toolbar(assign), instance.node);
          program.destroy = instance.destroy;
        }).catch((failure) => slot.replaceChildren(errorBox(failure)));
      }
      holder.replaceChildren(program.node);
    } else if (view === 'text') {
      text = text || textView(app);
      holder.replaceChildren(text.node);
      text.start();
    }
  }
  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.templatesView && views.some((item) => item.key === payload.templatesView)) {
        view = payload.templatesView;
        chips.set(view);
        started = true;
        paint();
        return;
      }
      if (!started && view) { started = true; paint(); }
    },
    destroy() { text?.destroy(); program?.destroy?.(); },
  };
}

function textView(app) {
  const node = h('div', 'ak-stack');
  const filter = { kind: '', scope: '', q: '' };
  let meta = null;
  const kind = selectBox(optionsOf(KIND_LABELS, { empty: 'Bütün türler' }), '', (value) => { filter.kind = value; list.reload(); },
    { label: 'Tür' });
  const scope = selectBox([{ value: '', label: 'Hazır ve kurum' }, { value: 'built_in', label: 'Hazır' },
    { value: 'institution', label: 'Kurum' }], '', (value) => { filter.scope = value; list.reload(); }, { label: 'Kapsam' });
  const search = searchBox({ placeholder: 'Şablon ara', label: 'Şablon ara', onSearch: (value) => { filter.q = value; list.reload(); } });
  const add = actionButton(app, 'admin_createCoachTextTemplate', 'Kurum şablonu ekle', { variant: 'primary',
    onClick: () => edit(null) });
  node.append(hintBox('Koçlar ve öğretmenler bu şablonları görüşme notu, soru yanıtı, check-in yorumu, veli görüşmesi, '
    + 'mesaj ve tebrik ekranlarında “Şablondan doldur” ile kullanır; öğrencinin adı, velisi, koçu, ders ve tarih '
    + 'kendiliğinden doldurulur. Koçların kişisel şablonları yalnız kendilerine görünür.'),
  toolbar(kind.node, scope.node, search.node, h('span', 'kit-spacer'), add));

  const list = staticList({
    load: async () => {
      meta = await app.api.get(TEXT_TEMPLATES, Object.fromEntries(Object.entries({
        kind: filter.kind, scope: filter.scope, includeInactive: 'true' }).filter(([, value]) => value)));
      const items = filterTemplates(meta?.items || [], { q: filter.q, includeInactive: true })
        .filter((item) => item.scope !== 'personal');
      add.hidden = meta?.canCurate === false;
      return items;
    },
    onRow: (row) => openTemplate(row),
    columns: [
      { key: 'title', label: 'Şablon', width: 'minmax(0, 2.4fr)', cell: (row) => {
        const cell = h('div', 'ak-tt-cell');
        cell.append(h('span', '', row.title), h('small', '', preview(row.body, 110)));
        return cell;
      } },
      { key: 'kind', label: 'Tür', width: '140px', cell: (row) => KIND_LABELS[row.kind] || row.kind },
      { key: 'scope', label: 'Kapsam', width: '100px', cell: (row) => badge(SCOPE_LABELS[row.scope] || row.scope, SCOPE_TONES[row.scope]) },
      { key: 'usage', label: 'Kullanım', width: '90px', align: 'num', cell: (row) => String(row.usageCount ?? 0) },
      { key: 'active', label: 'Durum', width: '90px', cell: (row) => (row.active ? badge('Etkin', 'good') : badge('Pasif', 'dim')) },
    ],
    empty: { title: 'Şablon yok', text: 'Süzgeci değiştirin ya da “Kurum şablonu ekle” ile yeni şablon yazın.' },
  });
  node.append(list.node);
  const refresh = () => { invalidateTemplates(app); list.reload(); };

  function openTemplate(row) {
    const view = openDrawer(app, { title: row.title, subtitle: `${KIND_LABELS[row.kind] || row.kind} · ${SCOPE_LABELS[row.scope] || ''}` });
    const parts = [kv([
      ['Tür', KIND_LABELS[row.kind] || row.kind],
      ['Kapsam', badge(SCOPE_LABELS[row.scope] || row.scope, SCOPE_TONES[row.scope])],
      ['Durum', row.active ? 'Etkin (seçicide görünür)' : 'Pasif (seçicide görünmez)'],
      ['Kullanım', `${row.usageCount ?? 0} kez${row.lastUsedAt ? ` · son ${fmtDateTime(row.lastUsedAt)}` : ''}`],
      row.kind === 'meeting_note' && row.noteVisibility ? ['Varsayılan görünürlük', NOTE_VISIBILITY[row.noteVisibility] || row.noteVisibility] : null,
      row.placeholders?.length ? ['Yer tutucular', row.placeholders.map((key) => PLACEHOLDER_LABELS[key] || key).join(', ')] : null,
    ].filter(Boolean)), h('pre', 'ak-tt-body', row.body)];
    if (row.actionItems?.length) {
      const items = h('ul', 'ak-coach-qlist');
      for (const item of row.actionItems) items.append(h('li', '', item));
      parts.push(h('h4', 'ak-coach-sub', 'Hazır eylem maddeleri'), items);
    }
    const actions = h('div', 'ak-row-actions');
    if (row.canEdit) {
      if (row.scope !== 'built_in') {
        actions.append(actionButton(app, 'admin_updateCoachTextTemplate', 'Düzenle', { variant: 'primary',
          onClick: async () => { if (await edit(row)) view.close(); } }));
      }
      actions.append(actionButton(app, 'admin_updateCoachTextTemplate', row.active ? 'Pasifleştir' : 'Etkinleştir', {
        variant: 'ghost', onClick: async () => {
          const done = await runAction(app, () => app.api.patch(`${TEXT_TEMPLATES}/${encodeURIComponent(row.id)}`,
            { active: !row.active }), { success: row.active ? 'Şablon pasifleştirildi.' : 'Şablon etkinleştirildi.' });
          if (done) { view.close(); refresh(); }
        } }));
      if (row.scope !== 'built_in') {
        actions.append(actionButton(app, 'admin_deleteCoachTextTemplate', 'Sil', { variant: 'ghost',
          onClick: async () => { if (await remove(row)) view.close(); } }));
      }
    }
    if (meta?.canCurate) {
      actions.append(actionButton(app, 'admin_createCoachTextTemplate', 'Kopyala (kurum şablonu)', { variant: 'ghost',
        onClick: async () => { if (await edit({ ...row, id: null, scope: 'institution', title: `${row.title} (kurum)`.slice(0, 80) }, true)) view.close(); } }));
    }
    if (actions.childElementCount) parts.push(actions);
    view.body.replaceChildren(...parts);
  }

  async function edit(row, copy = false) {
    const creating = !row || copy;
    const kindKey = row?.kind || filter.kind || 'meeting_note';
    const result = await formDialog(app, {
      title: creating ? 'Kurum şablonu' : 'Şablonu düzenle',
      description: TEXTS.hint,
      wide: true,
      fields: [
        creating && { key: 'kind', label: 'Tür', type: 'select', required: true, options: optionsOf(KIND_LABELS) },
        { key: 'title', label: 'Başlık', required: true, maxLength: 80 },
        { key: 'body', label: 'Metin', type: 'textarea', required: true, maxLength: 4000, wide: true,
          hint: 'Sınır türe göre: görüşme notu 4000, soru yanıtı ve mesaj 2000, check-in yorumu 1000, veli görüşmesi 300, tebrik 280 karakter.' },
        { key: 'actionItems', label: 'Hazır eylem maddeleri (her satıra bir; yalnız görüşme notu ve veli görüşmesi)',
          type: 'textarea', maxLength: 3100, wide: true },
        { key: 'noteVisibility', label: 'Varsayılan görünürlük (görüşme notu)', type: 'select',
          options: [{ value: '', label: 'Seçilmedi' }, ...optionsOf(NOTE_VISIBILITY)] },
        { key: 'sort', label: 'Sıra (küçük önce)', type: 'number', min: 0, max: 10000 },
        { key: 'active', label: 'Etkin (seçicide görünür)', type: 'checkbox' },
      ],
      value: { kind: kindKey, title: row?.title || '', body: row?.body || '', actionItems: actionsText(row?.actionItems),
        noteVisibility: row?.noteVisibility || '', sort: row?.sort ?? 100, active: row?.active ?? true },
      submitLabel: 'Kaydet',
      onSubmit: (draft, { key }) => {
        const kindNow = creating ? draft.kind : row.kind;
        const built = templateBody({ ...draft, actionItems: ACTION_KINDS.includes(kindNow) ? draft.actionItems : '',
          noteVisibility: draft.noteVisibility || null }, { kind: kindNow, scope: creating ? 'institution' : null,
          maxLengths: meta?.maxLengths || MAX_LENGTH, partial: !creating });
        if (built.error) throw new Error(built.error);
        if (!creating) {
          delete built.body.scope;
          return app.api.patch(`${TEXT_TEMPLATES}/${encodeURIComponent(row.id)}`, built.body);
        }
        return app.api.post(TEXT_TEMPLATES, built.body, { key });
      },
    });
    if (!result) return false;
    app.toast(TEXTS.saved, 'good');
    refresh();
    return true;
  }

  async function remove(row) {
    const yes = await confirmAction(app, { title: `“${row.title}” silinsin mi?`,
      description: 'Şablon seçicilerden kalkar. Bu şablonla daha önce yazılmış notlar ve mesajlar etkilenmez.',
      confirmLabel: 'Sil', danger: true });
    if (!yes) return false;
    const done = await runAction(app, async () => {
      await app.api.del(`${TEXT_TEMPLATES}/${encodeURIComponent(row.id)}`);
      return true;
    }, { success: 'Şablon silindi.' });
    if (done) refresh();
    return Boolean(done);
  }

  let started = false;
  return {
    node,
    start() { if (!started) { started = true; list.reload(); } },
    destroy() { search.destroy?.(); },
  };
}
