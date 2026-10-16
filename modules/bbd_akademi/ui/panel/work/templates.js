// BBD Akademi — Şablonlar (F-A25 / F-A26): tek yerde ödev şablonu, atama paketi, plan şablonu.
//
// KULLANICI: "karışık olmasın". "Bir kez hazırla, tek tıkla ver" işlerinin
// hepsi bu ekranın üç sekmesindedir:
//
//   · Ödev şablonları — kurum şablonu (KM'den açılan şablon kurumundur; kişisel
//     şablon kişinin kendi uygulamasındadır). "Bu şablonla ödev ver" toplu ödev
//     sihirbazını şablon seçili açar. Liste `admin_listTemplates?kind=homework`
//     (tek "Şablonlar" kartı), ayrıntı `admin_getHomeworkTemplate`.
//   · Atama paketleri — program + kaynak + kaynak paketi + plan şablonu birlikte;
//     TANIMI YALNIZ KM'de (sözleşme `x-surface: km`), uygulamayı yönetici mobilde
//     de yapar. Uygula / kaldır önce sayar (önizleme), 300 öğrenciyi aşan iş
//     arka planda koşar (`BulkJob`).
//   · Plan şablonları — Çalışma planı ekranındaki şablon yönetiminin aynısı.
//
// Kurala uyan YENİ öğrenciye paketin kendiliğinden uygulanması için hem
// paketin "otomatik" kutusu hem kurum ayarı (Ayarlar → Kurum ayarları →
// Atama paketleri) açık olmalı.

import { h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow, tabBar } from '../../../ui-kit/layout.js';
import { AkademiError } from '../api.js';
import {
  actionButton, confirmAction, fmtDate, fmtDateTime, formDialog, kv, loader, num, openDrawer,
  pagedList, problem, runAction, searchBox, sectionHead, selectBox, todayIst, toolbar,
} from '../common.js';
import { bulkTargetField } from '../shared/bulk-target.js';
import { programLabel, sortPrograms } from '../shared/curriculum-picker.js';
import { bulkHomeworkWizard, contentDialog, templateLine } from './homework-bulk.js';
import { topicsText } from './homework-content.js';
import { templatesTab as planTemplatesTab } from './plan-templates.js';
import { gradeValue, loadPackages, loadTargets, resourceListField, ruleText } from '../resources/resources-common.js';
import { rulesField } from '../resources/resources-packages.js';
import { bulkJobDialog } from '../shared/wizard.js';

const SCOPE = { institution: ['Kurum', 'info'], personal: ['Kişisel', 'dim'] };
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

export function section(app) {
  loadStyles(import.meta.url, 'plan.css');
  const node = h('div', 'ak-stack ak-tpl');
  node.append(sectionHead('Şablonlar', {
    subtitle: 'Bir kez hazırlayın, tek tıkla verin: ödev şablonları, atama paketleri (program + kaynak + plan) '
      + 've çalışma planı şablonları.' }));

  const bodies = {};
  const builders = {
    homework: () => homeworkTab(app),
    bundles: () => bundlesTab(app),
    plan: () => {
      const tab = planTemplatesTab(app, { onApplied: (assignment) => app.go('plan',
        { planAssignmentId: assignment.id }) });
      return tab;
    },
  };
  const tabs = tabBar([
    { key: 'homework', label: 'Ödev şablonları' },
    { key: 'bundles', label: 'Atama paketleri' },
    { key: 'plan', label: 'Plan şablonları' },
  ], 'homework', (key) => show(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload?.();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && builders[payload.tab] ? payload.tab : null;
      if (!started) {
        started = true;
        if (wanted && wanted !== 'homework') tabs.select(wanted, false);
        show(wanted || 'homework');
      } else if (wanted) {
        tabs.select(wanted);
      }
    },
    destroy() { for (const body of Object.values(bodies)) body.destroy?.(); },
  };
}

// ========================================================= ödev şablonları

function homeworkTab(app) {
  const node = h('div', 'ak-stack');
  let q = '';
  const search = searchBox({ placeholder: 'Şablon ara (en az 2 harf)', label: 'Ödev şablonu ara',
    onSearch: (text) => { q = text.length >= 2 ? text : ''; list.reload(); } });
  const add = actionButton(app, 'admin_createHomeworkTemplate', 'Yeni ödev şablonu', { variant: 'primary',
    onClick: async () => {
      const saved = await templateForm(app, null);
      if (saved) { app.toast(`“${saved.name}” kaydedildi.`, 'good'); list.reload(); }
    } });
  node.append(toolbar(search.node, h('span', 'kit-spacer'), add));

  const list = pagedList({
    app,
    path: '/v1/admin/templates',
    query: () => ({ kind: 'homework', q: q || undefined }),
    limit: 30,
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const link = h('button', 'ak-link', row.name);
        link.type = 'button';
        link.addEventListener('click', () => openTemplate(row.id));
        cell.append(link, h('small', '', row.subtitle || row.description || ''));
        return cell;
      } },
      { key: 'scope', label: 'Tür', width: '90px', cell: (row) => badge(...(SCOPE[row.scope] || [row.scope, 'dim'])) },
      { key: 'usage', label: 'Verilen ödev', width: '110px', align: 'num', cell: (row) => num(row.usageCount) },
      { key: 'updated', label: 'Güncelleme', width: '110px', cell: (row) => fmtDate(row.updatedAt) },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_createHomeworkBulk', 'Ödev ver', { variant: 'ghost',
          onClick: () => giveFrom(row.id) }));
        return actions;
      } },
    ],
    empty: { title: 'Henüz ödev şablonu yok', text: 'Sık verdiğiniz ödevleri şablon yapın; sonra sınıflara tek '
      + 'tıkla verin.', actions: [actionButton(app, 'admin_createHomeworkTemplate', 'Yeni ödev şablonu',
      { variant: 'primary', onClick: () => add.click() })] },
  });
  node.append(list.node);

  async function giveFrom(id) {
    const template = await runAction(app, () => app.api.get(`/v1/admin/homework-templates/${id}`));
    if (!template) return;
    const job = await bulkHomeworkWizard(app, { template });
    if (job) list.reload();
  }

  function openTemplate(id) {
    const panel = openDrawer(app, { title: 'Ödev şablonu' });
    const view = loader(() => app.api.get(`/v1/admin/homework-templates/${id}`), (template) => {
      panel.setTitle(template.name);
      const tools = h('div', 'ak-inline');
      tools.append(actionButton(app, 'admin_createHomeworkBulk', 'Bu şablonla ödev ver', { variant: 'primary',
        onClick: async () => {
          panel.close();
          const job = await bulkHomeworkWizard(app, { template });
          if (job) list.reload();
        } }));
      if (template.editable !== false) {
        tools.append(actionButton(app, 'admin_updateHomeworkTemplate', 'Düzenle', { onClick: async () => {
          const saved = await templateForm(app, template);
          if (saved) { app.toast('Şablon güncellendi.', 'good'); view.reload(); list.reload(); }
        } }));
      }
      tools.append(actionButton(app, 'admin_copyHomeworkTemplate', 'Kopyala', { onClick: () => copy(template) }));
      if (template.editable !== false) {
        tools.append(actionButton(app, 'admin_deleteHomeworkTemplate', 'Sil', { variant: 'ghost',
          onClick: () => remove(template) }));
      }
      return [tools, card('', kv([
        ['Ödev başlığı', template.title],
        ['Ders', template.subject?.name || '—'],
        ['Konular', topicsText(template.topics, 50) || '—'],
        ['Kaynak', template.resourceId ? [
          (template.sectionIds || []).length ? `${num(template.sectionIds.length)} bölüm` : '',
          template.pageFrom || template.pageTo ? `s. ${template.pageFrom ?? '…'}–${template.pageTo ?? '…'}` : '',
        ].filter(Boolean).join(' · ') || 'Seçili' : '—'],
        ['Açıklama', template.description || '—'],
        ['Varsayılan süre', templateLine({ ...template, title: '', subject: null })],
        ['Tür', `${SCOPE[template.scope]?.[0] || template.scope}${template.owner ? ` · ${template.owner.displayName}` : ''}`],
        ['Verilen ödev', num(template.usageCount)],
        ['Güncelleme', fmtDateTime(template.updatedAt)],
      ]))];
    });
    panel.body.append(view.node);
    view.reload();

    async function copy(template) {
      const saved = await formDialog(app, {
        title: `“${template.name}” — kopyala`,
        description: 'Kopya kurum şablonu olarak kaydedilir; sonra ayrı düzenlenir.',
        value: { name: `${template.name} (kopya)`.slice(0, 80) },
        fields: [{ key: 'name', label: 'Yeni şablonun adı', required: true, maxLength: 80 }],
        submitLabel: 'Kopyala',
        onSubmit: (draft, { key }) => app.api.post(`/v1/admin/homework-templates/${template.id}/copy`,
          { name: String(draft.name || '').trim(), scope: 'institution' }, { key }),
      });
      if (saved && saved !== true) {
        app.toast(`“${saved.name}” oluşturuldu.`, 'good');
        list.reload();
        panel.close();
        openTemplate(saved.id);
      }
    }

    async function remove(template) {
      const yes = await confirmAction(app, { title: `“${template.name}” silinsin mi?`,
        description: 'Bu şablondan verilmiş ödevler etkilenmez; yalnız şablon kalkar.',
        confirmLabel: 'Şablonu sil', danger: true });
      if (!yes) return;
      const ok = await runAction(app, async () => {
        await app.api.del(`/v1/admin/homework-templates/${template.id}`);
        return true;
      }, { success: 'Şablon silindi.' });
      if (ok) { panel.close(); list.reload(); }
    }
  }

  return { node, reload: () => list.reload(), destroy: () => { search.destroy(); list.destroy(); } };
}

/**
 * Ödev şablonu oluştur / düzenle: ad + ödev içeriği + varsayılan süre.
 * KM'den açılan şablon kurum şablonudur (`scope: institution`); kişisel şablon
 * bir kişiye bağlıdır ve entegrasyon anahtarıyla açılamaz.
 */
async function templateForm(app, current) {
  return contentDialog(app, current, {
    title: current ? `“${current.name}” — düzenle` : 'Yeni ödev şablonu',
    submitLabel: current ? 'Kaydet' : 'Şablonu kaydet',
    extraValue: { name: current?.name || '', dueAfterDays: current?.dueAfterDays ?? 7,
      dueTime: current?.dueTime || '23:59' },
    extraFields: [
      { key: 'name', label: 'Şablon adı', required: true, maxLength: 80, wide: true, first: true,
        placeholder: 'Ör. Paragraf seti (haftalık)' },
      { key: 'dueAfterDays', label: 'Varsayılan süre (gün)', type: 'number', min: 0, max: 60, required: true,
        hint: 'Ödev verildiği günden kaç gün sonra teslim (0 = aynı gün).' },
      { key: 'dueTime', label: 'Teslim saati', maxLength: 5, placeholder: '23:59', required: true,
        validate: (text) => (CLOCK.test(String(text || '').trim()) ? null : 'Saati 18:30 gibi yazın.') },
    ],
    save: ({ draft, content }, { key }) => {
      const body = {
        ...content,
        name: String(draft.name || '').trim(),
        scope: current?.scope || 'institution',
        dueAfterDays: Number(draft.dueAfterDays),
        dueTime: String(draft.dueTime || '').trim(),
      };
      return current ? app.api.put(`/v1/admin/homework-templates/${current.id}`, body)
        : app.api.post('/v1/admin/homework-templates', body, { key });
    },
  });
}

// ========================================================= atama paketleri

function contentsText(bundle) {
  return [
    (bundle.programs || []).length ? `${num(bundle.programs.length)} program` : '',
    (bundle.resources || []).length ? `${num(bundle.resources.length)} kaynak` : '',
    (bundle.resourcePackages || []).length ? `${num(bundle.resourcePackages.length)} kaynak paketi` : '',
    bundle.studyPlanTemplate ? 'plan şablonu' : '',
  ].filter(Boolean).join(' · ') || '—';
}

function bundlesTab(app) {
  const node = h('div', 'ak-stack');
  let q = '';
  const search = searchBox({ placeholder: 'Paket ara (en az 2 harf)', label: 'Atama paketi ara',
    onSearch: (text) => { q = text.length >= 2 ? text : ''; list.reload(); } });
  const add = actionButton(app, 'admin_createAssignmentBundle', 'Yeni atama paketi', { variant: 'primary',
    onClick: async () => {
      const saved = await bundleForm(app, null);
      if (saved) { app.toast(`“${saved.name}” oluşturuldu. Öğrencilere vermek için “Uygula”.`, 'good'); list.reload(); }
    } });
  node.append(toolbar(search.node, h('span', 'kit-spacer'),
    h('span', 'ak-muted', 'Örn. “12 SAY dönem başı”: program + kaynaklar + plan tek seferde.'), add));

  const list = pagedList({
    app,
    path: '/v1/admin/assignment-bundles',
    query: () => ({ q: q || undefined }),
    limit: 30,
    columns: [
      { key: 'name', label: 'Paket', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const link = h('button', 'ak-link', row.name);
        link.type = 'button';
        link.addEventListener('click', () => openBundle(app, row.id, { onChange: () => list.reload() }));
        cell.append(link);
        if (row.description) cell.append(h('small', '', row.description));
        return cell;
      } },
      { key: 'contents', label: 'İçerik', width: 'minmax(0, 1.6fr)', cell: (row) => contentsText(row) },
      { key: 'rules', label: 'Kural', width: 'minmax(0, 1.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(row.autoApply ? badge('Yeni öğrenciye otomatik', 'good') : h('span', 'ak-muted', 'Elle'));
        if ((row.rules || []).length) cell.append(h('small', '', row.rules.map(ruleText).join(' | ')));
        return cell;
      } },
      { key: 'applied', label: 'Uygulanan öğrenci', width: '130px', align: 'num',
        cell: (row) => num(row.appliedStudentCount) },
      { key: 'actions', label: '', width: '150px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_applyAssignmentBundle', 'Uygula', { variant: 'ghost',
          onClick: async () => { if (await applyBundle(app, row)) list.reload(); } }));
        return actions;
      } },
    ],
    empty: { title: 'Henüz atama paketi yok', text: 'Dönem başında sınıfa ya da kurala uyan herkese program, '
      + 'kaynak ve çalışma planını tek seferde vermek için paket hazırlayın.',
    actions: [actionButton(app, 'admin_createAssignmentBundle', 'Yeni atama paketi', { variant: 'primary',
      onClick: () => add.click() })] },
  });
  node.append(list.node);
  return { node, reload: () => list.reload(), destroy: () => { search.destroy(); list.destroy(); } };
}

function openBundle(app, id, { onChange } = {}) {
  const panel = openDrawer(app, { title: 'Atama paketi' });
  const view = loader(() => app.api.get(`/v1/admin/assignment-bundles/${id}`), (bundle) => {
    panel.setTitle(bundle.name);
    const tools = h('div', 'ak-inline');
    tools.append(
      actionButton(app, 'admin_applyAssignmentBundle', 'Uygula', { variant: 'primary', onClick: async () => {
        if (await applyBundle(app, bundle)) { view.reload(); onChange?.(); }
      } }),
      actionButton(app, 'admin_removeAssignmentBundle', 'Öğrencilerden kaldır', { onClick: async () => {
        if (await removeBundle(app, bundle)) { view.reload(); onChange?.(); }
      } }),
      actionButton(app, 'admin_updateAssignmentBundle', 'Düzenle', { onClick: async () => {
        const saved = await bundleForm(app, bundle);
        if (saved) { app.toast('Paket güncellendi.', 'good'); view.reload(); onChange?.(); }
      } }),
      actionButton(app, 'admin_deleteAssignmentBundle', 'Sil', { variant: 'ghost', onClick: async () => {
        const yes = await confirmAction(app, { title: `“${bundle.name}” silinsin mi?`,
          description: 'Paket arşivlenir; öğrencilere açılmış atamalar YERİNDE KALIR. Kaldırmak için önce '
            + '“Öğrencilerden kaldır”.', confirmLabel: 'Paketi sil', danger: true });
        if (!yes) return;
        const ok = await runAction(app, async () => { await app.api.del(`/v1/admin/assignment-bundles/${id}`); return true; },
          { success: 'Paket silindi.' });
        if (ok) { panel.close(); onChange?.(); }
      } }),
    );
    const programs = (bundle.programs || []).map((item) => `${item.subject?.name || ''} — ${item.title}`);
    const resources = (bundle.resources || []).map((item) => item.title);
    const packages = (bundle.resourcePackages || []).map((item) => `${item.name} (${num(item.resourceCount)} kaynak)`);
    return [tools, card('', kv([
      ['Açıklama', bundle.description || '—'],
      ['Müfredat programları', programs.length ? programs.join(' · ') : '—'],
      ['Kaynaklar', resources.length ? resources.join(' · ') : '—'],
      ['Kaynak paketleri', packages.length ? packages.join(' · ') : '—'],
      ['Çalışma planı', bundle.studyPlanTemplate ? `${bundle.studyPlanTemplate.name} (${num(bundle.studyPlanTemplate
        .durationDays)} gün) · uygulamadan ${num(bundle.planStartOffsetDays)} gün sonra başlar` : '—'],
      ['Kurallar', (bundle.rules || []).length ? bundle.rules.map(ruleText).join(' | ') : '—'],
      ['Yeni öğrenciye otomatik', bundle.autoApply ? 'Açık (kurum ayarı da açıksa)' : 'Kapalı'],
      ['Uygulanan öğrenci', num(bundle.appliedStudentCount)],
      ['Güncelleme', fmtDateTime(bundle.updatedAt)],
    ]))];
  });
  panel.body.append(view.node);
  view.reload();
  return panel;
}

/** Müfredat programı seçici: ders → program, seçilenler çip. `chosen` Map(id → etiket). */
function programPicker(app, subjects, chosen, onChange) {
  const node = h('div', 'ak-stack');
  const chips = h('div', 'ak-chips');
  const paint = () => {
    chips.replaceChildren();
    for (const [id, label] of chosen) {
      const chip = h('button', 'kit-chip on', `${label} ✕`);
      chip.type = 'button';
      chip.setAttribute('aria-label', `${label} — çıkar`);
      chip.addEventListener('click', () => { chosen.delete(id); paint(); onChange(); });
      chips.append(chip);
    }
    if (!chosen.size) chips.append(h('span', 'ak-muted', 'Program seçilmedi.'));
  };
  const programSelect = selectBox([{ value: '', label: 'Önce ders seçin' }], '', (id) => {
    if (!id) return;
    const option = programOptions.find((item) => item.value === id);
    chosen.set(id, option?.label || id);
    programSelect.set('');
    paint();
    onChange();
  }, { label: 'Program' });
  let programOptions = [];
  const subjectSelect = selectBox([{ value: '', label: 'Ders seçin' },
    ...subjects.map((item) => ({ value: item.id, label: item.name }))], '', async (subject) => {
    programOptions = [];
    programSelect.options([{ value: '', label: subject ? 'Programlar yükleniyor…' : 'Önce ders seçin' }]);
    if (!subject) return;
    try {
      const data = await app.api.getCached('/v1/admin/curricula/programs', { subject, scope: 'all' });
      // Seviye sırası ve seviyeli ad ("11. sınıf · Matematik"): aynı adlı programlar ayrışır.
      programOptions = sortPrograms(data?.items || []).map((item) => ({ value: item.id,
        label: programLabel(item) }));
      programSelect.options([{ value: '', label: programOptions.length ? 'Program ekle…' : 'Bu derste program yok' },
        ...programOptions]);
    } catch {
      programSelect.options([{ value: '', label: 'Programlar okunamadı' }]);
    }
  }, { label: 'Ders' });
  const row = h('div', 'ak-inline');
  row.append(subjectSelect.node, programSelect.node);
  node.append(row, chips);
  paint();
  return node;
}

/** Atama paketi oluştur / düzenle (yalnız KM). */
async function bundleForm(app, current) {
  const [subjects, packages, planTemplates] = await Promise.all([
    app.lookups.subjects().catch(() => []),
    loadPackages(app).catch(() => []),
    app.can('admin_listStudyPlanTemplates')
      ? app.api.all('/v1/admin/plan/templates', {}, { limit: 100, max: 300 }).catch(() => []) : Promise.resolve([]),
  ]);
  const programs = new Map((current?.programs || []).map((item) => [item.id,
    `${item.subject?.name || ''} — ${item.title}`]));
  let resources = [...(current?.resources || [])];
  const packageIds = new Set((current?.resourcePackages || []).map((item) => item.id));
  const rules = (current?.rules || []).map((rule) => ({
    gradeLevel: rule.gradeLevel === null || rule.gradeLevel === undefined ? '' : String(rule.gradeLevel),
    track: rule.track || '',
    examTarget: rule.examTarget || '',
  }));
  const planOptions = [{ value: '', label: 'Plan şablonu yok' },
    ...planTemplates.map((item) => ({ value: item.id, label: `${item.name} (${num(item.durationDays)} gün)` }))];
  if (current?.studyPlanTemplate && !planTemplates.some((item) => item.id === current.studyPlanTemplate.id)) {
    planOptions.push({ value: current.studyPlanTemplate.id, label: current.studyPlanTemplate.name });
  }
  const empty = () => !programs.size && !resources.length && !packageIds.size;

  const saved = await formDialog(app, {
    title: current ? `“${current.name}” — düzenle` : 'Yeni atama paketi',
    description: 'Paket uygulanınca öğrenciye programlar (istisna olarak), kaynaklar ve plan şablonu birlikte '
      + 'verilir; paketi almış öğrenci atlanır.',
    wide: true,
    value: {
      name: current?.name || '',
      description: current?.description || '',
      plan: current?.studyPlanTemplate?.id || '',
      offset: current?.planStartOffsetDays ?? 0,
      autoApply: Boolean(current?.autoApply),
    },
    fields: [
      { key: 'name', label: 'Paket adı', required: true, maxLength: 80, placeholder: '12 SAY dönem başı' },
      { key: 'description', label: 'Açıklama', maxLength: 300 },
      { key: 'programs', label: 'Müfredat programları', type: 'custom',
        render: () => programPicker(app, subjects, programs, () => {}) },
      { key: 'resources', label: 'Kaynaklar', type: 'custom', render: () => resourceListField(app, {
        initial: resources, onChange: (list) => { resources = list; } }).node },
      { key: 'packages', label: 'Kaynak paketleri', type: 'custom', render: () => {
        if (!packages.length) return h('span', 'ak-muted', 'Kaynak paketi yok (Kaynaklar → Paketler).');
        const wrap = h('div', 'ak-chips');
        wrap.setAttribute('role', 'group');
        wrap.setAttribute('aria-label', 'Kaynak paketleri');
        for (const item of packages) {
          const chip = h('button', `kit-chip${packageIds.has(item.id) ? ' on' : ''}`, item.name);
          chip.type = 'button';
          chip.setAttribute('aria-pressed', packageIds.has(item.id) ? 'true' : 'false');
          chip.addEventListener('click', () => {
            if (packageIds.has(item.id)) packageIds.delete(item.id); else packageIds.add(item.id);
            chip.classList.toggle('on', packageIds.has(item.id));
            chip.setAttribute('aria-pressed', packageIds.has(item.id) ? 'true' : 'false');
          });
          wrap.append(chip);
        }
        return wrap;
      } },
      { key: 'plan', label: 'Çalışma planı şablonu', type: 'select', options: planOptions },
      { key: 'offset', label: 'Plan kaç gün sonra başlasın?', type: 'number', min: 0, max: 60,
        hint: 'Uygulama gününden itibaren (0 = aynı gün).' },
      { key: 'rules', label: 'Kurallar (kime uygun?)', type: 'custom', render: () => rulesField(rules, () => {}),
        hint: 'Ör. “12. sınıf · Sayısal · YKS”. “Uygula → kuralına uyan herkes” ve otomatik uygulama bunlara bakar.',
        validate: (_raw, draft) => {
          if (rules.some((rule) => !rule.gradeLevel && !rule.track && !rule.examTarget)) {
            return 'Her kuralda en az bir koşul seçin.';
          }
          if (draft.autoApply && !rules.length) return 'Otomatik uygulama için en az bir kural gerekir.';
          return null;
        } },
      { key: 'autoApply', label: 'Kurala uyan YENİ öğrenciye kendiliğinden uygula', type: 'checkbox',
        hint: 'Kurum ayarı “Atama paketlerini yeni öğrencilere uygula” da açık olmalı (Ayarlar → Kurum ayarları).' },
    ],
    submitLabel: current ? 'Kaydet' : 'Paketi oluştur',
    onSubmit: (draft, { key }) => {
      if (empty()) throw new AkademiError({ code: 'empty_bundle', message: 'Pakete en az bir program, kaynak ya da '
        + 'kaynak paketi ekleyin.' });
      const body = {
        name: String(draft.name || '').trim(),
        description: String(draft.description || '').trim() || null,
        programIds: [...programs.keys()],
        resourceIds: resources.map((item) => item.id),
        resourcePackageIds: [...packageIds],
        studyPlanTemplateId: draft.plan || null,
        planStartOffsetDays: Number(draft.offset) || 0,
        rules: rules.map((rule) => ({ gradeLevel: gradeValue(rule.gradeLevel), track: rule.track || null,
          examTarget: rule.examTarget || null })),
        autoApply: Boolean(draft.autoApply),
      };
      return current ? app.api.put(`/v1/admin/assignment-bundles/${current.id}`, body)
        : app.api.post('/v1/admin/assignment-bundles', body, { key });
    },
  });
  return saved && saved !== true ? saved : null;
}

/** Hedef seçicili pencere (uygula / kaldır) için ortak alan. */
async function targetPicker(app, bundle) {
  const [classes, groups] = await loadTargets(app);
  const rulesText = (bundle.rules || []).map(ruleText).join(' | ');
  return { classes, groups, rulesText, modes: rulesText ? ['pick', 'filter', 'rules'] : ['pick', 'filter'] };
}

function previewKpis(result) {
  const nodes = [kpiRow([
    { label: 'Öğrenci', value: num(result.studentCount) },
    { label: 'Zaten almış', value: num(result.alreadyAppliedCount) },
    { label: 'Hariç', value: num(result.excludedCount) },
    { label: 'Program istisnası', value: num(result.programAdds) },
    { label: 'Yeni kaynak ataması', value: num(result.resourceAssignments?.created),
      title: `${num(result.resourceAssignments?.alreadyAssigned)} zaten atanmış` },
    { label: 'Plan görevi', value: num(result.planTaskCount) },
  ])];
  if ((result.groups || []).length) {
    nodes.push(h('div', 'ak-muted', result.groups.map((group) => `${group.label}: ${num(group.studentCount)} öğrenci`)
      .join(' · ')));
  }
  if (result.runsInBackground) {
    nodes.push(alertBox('300 öğrenciden fazla: işlem arka planda koşar, ilerleme pencerede görünür.', 'info'));
  }
  return nodes;
}

/**
 * Paketi uygula: kime (seçerek / süzgeçle / kuralına uyan herkes) → önce say → uygula.
 * @returns {Promise<object|null>} biten iş
 */
async function applyBundle(app, bundle) {
  const spec = await targetPicker(app, bundle);
  let target = null;
  const previewSlot = h('div', 'ak-stack');
  previewSlot.setAttribute('aria-live', 'polite');
  previewSlot.append(h('div', 'ak-muted', '“Önce say” hiçbir şey yazmadan kaç öğrenciye ne düşeceğini gösterir.'));
  const body = (draft) => ({ target: target.value({ bundle: true }), startDate: draft.startDate || null,
    notify: Boolean(draft.notify) });

  const job = await formDialog(app, {
    title: `“${bundle.name}” — uygula`,
    description: `${contentsText(bundle)}. Paketi zaten almış öğrenci atlanır.`,
    wide: true,
    value: { startDate: '', notify: true },
    fields: [
      { key: 'target', label: 'Kime', type: 'custom', render: () => {
        target = bulkTargetField(app, { ...spec, onChange: () => {} });
        return target.node;
      }, validate: () => target?.problem() || null },
      bundle.studyPlanTemplate ? { key: 'startDate', label: 'Plan başlangıcı (boşsa bugün + '
        + `${num(bundle.planStartOffsetDays || 0)} gün)`, type: 'date' } : null,
      { key: 'notify', label: 'Öğrencilere tek özet bildirim gönder', type: 'checkbox' },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = actionButton(app, 'admin_previewAssignmentBundle', 'Önce say', { onClick: async () => {
          const reason = target?.problem();
          if (reason) { previewSlot.replaceChildren(alertBox(reason, 'warn')); return; }
          go.disabled = true;
          try {
            const result = await app.api.post(`/v1/admin/assignment-bundles/${bundle.id}/preview`, body(draft));
            previewSlot.replaceChildren(h('div', 'ak-muted', 'Önizleme — henüz bir şey yazılmadı:'),
              ...previewKpis(result));
          } catch (failure) {
            previewSlot.replaceChildren(alertBox(problem(failure, 'Önizleme alınamadı.'), 'bad'));
          } finally {
            go.disabled = false;
          }
        } });
        wrap.append(go, previewSlot);
        return wrap;
      } },
    ].filter(Boolean),
    submitLabel: 'Uygula',
    onSubmit: (draft, { key }) => {
      if (draft.startDate && draft.startDate < todayIst()) {
        throw new AkademiError({ code: 'date_in_past', message: 'Plan başlangıcı geçmişte olamaz.' });
      }
      return app.api.post(`/v1/admin/assignment-bundles/${bundle.id}/apply`, body(draft), { key });
    },
  });
  target?.destroy();
  if (!job || job === true) return null;
  const done = ['queued', 'running'].includes(job.status)
    ? await bulkJobDialog(app, async () => job, { title: `“${bundle.name}” uygulanıyor…` }) : job;
  const result = done?.bundle;
  if (!result) return done;
  app.toast(`${num(result.studentCount)} öğrenciye uygulandı · ${num(result.programOverridesCreated)} program, `
    + `${num(result.resourceAssignmentsCreated)} kaynak ataması${result.planAssignmentId ? ', plan' : ''}`
    + `${result.alreadyAppliedCount ? ` · ${num(result.alreadyAppliedCount)} zaten almıştı` : ''}.`, 'good');
  return done;
}

/** Paketi öğrencilerden toplu kaldır (program istisnaları + kaynak atamaları). */
async function removeBundle(app, bundle) {
  const spec = await targetPicker(app, bundle);
  let target = null;
  const job = await formDialog(app, {
    title: `“${bundle.name}” — öğrencilerden kaldır`,
    description: 'Paketle açılan program istisnaları ve kaynak atamaları kaldırılır; öğrencinin kendi rafına '
      + 'eklediği kaynaklara dokunulmaz. Plan şablonuyla açılmış görevler SİLİNMEZ (gerekirse Çalışma planı → '
      + 'Atamalar\'dan kaldırın).',
    wide: true,
    danger: true,
    fields: [
      { key: 'target', label: 'Kimden', type: 'custom', render: () => {
        target = bulkTargetField(app, { ...spec, onChange: () => {} });
        return target.node;
      }, validate: () => target?.problem() || null },
    ],
    submitLabel: 'Kaldır',
    onSubmit: (_draft, { key }) => app.api.post(`/v1/admin/assignment-bundles/${bundle.id}/remove`,
      { target: target.value({ bundle: true }) }, { key }),
  });
  target?.destroy();
  if (!job || job === true) return null;
  const done = ['queued', 'running'].includes(job.status)
    ? await bulkJobDialog(app, async () => job, { title: `“${bundle.name}” kaldırılıyor…` }) : job;
  const result = done?.bundle;
  if (result) {
    app.toast(`${num(result.studentCount)} öğrenciden kaldırıldı · ${num(result.programOverridesRemoved)} program, `
      + `${num(result.resourceAssignmentsRemoved)} kaynak ataması.`, 'good');
  }
  return done;
}

