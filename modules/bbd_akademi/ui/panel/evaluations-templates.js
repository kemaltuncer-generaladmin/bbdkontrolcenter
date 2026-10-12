// BBD Akademi — Değerlendirme şablonları (F-A21, km).
//
// Şablon = bölümler → kriterler + ölçek (1–5 ya da dört düzey) + güçlü yön /
// gelişim alanı kutuları. Kritere isteğe bağlı bir OTOMATİK VERİ bağlanır
// (plan uyumu, soru, deneme trendi …); değerlendirme formunun yan panelinde
// koça yol gösterir, puanı kendisi vermez.
//
// SÜRÜM KURALI (Akademi): güncelleme `version`ı artırır; mevcut
// değerlendirmeler kendi kopyalarını korur. Dönemde kullanılmış şablonun
// ölçeği değişmez (409 `template_in_use`). Bölüm/kriter kimlikleri düzenlemede
// KORUNUR — gelişim grafiği kriteri kimliğinden izler. Şablon silinmez,
// arşivlenir (yeni döneme seçilemez).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, fmtDateTime, formDialog, kv, loader, num, openDrawer, pagedList, runAction,
  statusBadge,
} from './common.js';
import {
  AUTO_METRICS, SCALE_KINDS, TEMPLATE_STATUS, iconButton, scaleText, stackCell,
} from './evaluations-shared.js';

/** Yeni şablon için önerilen bölümler (FEATURES F-A21 örnek kriterleri). */
const PRESET_SECTIONS = [
  { title: 'Akademik gelişim', criteria: [
    { title: 'Akademik ilerleme', description: 'Deneme ve konu ilerlemesindeki gelişim.', autoMetric: 'exam_trend' },
    { title: 'Soru çözme düzeni', description: 'Düzenli ve yeterli soru çözümü.', autoMetric: 'questions' },
    { title: 'Ödev sorumluluğu', description: 'Ödevleri zamanında ve eksiksiz teslim etme.', autoMetric: 'homework_rate' },
  ] },
  { title: 'Çalışma alışkanlıkları', criteria: [
    { title: 'Çalışma düzeni ve plan uyumu', description: 'Haftalık plana uyma.', autoMetric: 'plan_adherence' },
    { title: 'Derse katılım', description: 'Derste etkin olma, soru sorma.', autoMetric: null },
    { title: 'Devam ve dakiklik', description: 'Derslere zamanında ve düzenli gelme.', autoMetric: 'attendance' },
  ] },
  { title: 'Kişisel gelişim', criteria: [
    { title: 'Motivasyon', description: 'Hedefe bağlılık ve istek.', autoMetric: 'self_review' },
    { title: 'Davranış ve iletişim', description: 'Arkadaşları ve öğretmenleriyle iletişim.', autoMetric: null },
  ] },
];

const metricOptions = [{ value: '', label: 'Otomatik veri yok' },
  ...Object.entries(AUTO_METRICS).map(([value, label]) => ({ value, label }))];

/** Şablonlar sekmesi. */
export function templatesTab(ctx) {
  const { app } = ctx;
  const node = h('div', 'ak-stack');
  let status = 'active';

  const add = actionButton(app, 'admin_createEvaluationTemplate', 'Şablon oluştur', { variant: 'primary',
    onClick: () => createTemplate() });
  const chips = chipRow([
    { key: 'active', label: 'Etkin' },
    { key: 'archived', label: 'Arşivde' },
    { key: 'all', label: 'Tümü' },
  ], status, (key) => { status = key || 'active'; chips.set(status); list.reload(); });
  const bar = h('div', 'ak-toolbar');
  bar.append(chips.node, h('span', 'kit-spacer'), add);
  node.append(bar);

  const list = pagedList({
    app,
    path: '/v1/admin/evaluation-templates',
    query: () => ({ status }),
    limit: 50,
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2fr)', cell: (row) => stackCell(row.name) },
      { key: 'scale', label: 'Ölçek', width: 'minmax(0, 1.3fr)', cell: (row) => SCALE_KINDS[row.scaleKind] || row.scaleKind },
      { key: 'criteria', label: 'Kriter', align: 'num', width: '70px', cell: (row) => num(row.criterionCount) },
      { key: 'version', label: 'Sürüm', align: 'num', width: '70px', cell: (row) => `v${row.version}` },
      { key: 'status', label: 'Durum', width: '100px', cell: (row) => statusBadge(TEMPLATE_STATUS, row.status) },
      { key: 'updated', label: 'Güncellendi', width: '130px', cell: (row) => fmtDateTime(row.updatedAt) },
    ],
    onRow: (row) => openTemplate(row.id),
    empty: { title: 'Bu durumda şablon yok',
      text: 'Değerlendirme dönemi açmak için önce bir şablon oluşturun (bölümler, kriterler, ölçek).' },
  });
  node.append(list.node);

  /** Özet satırı tam şablondan kurulur (listeyi baştan çekmeden). */
  function summaryOf(template) {
    return { id: template.id, name: template.name, scaleKind: template.scale?.kind,
      criterionCount: (template.sections || []).reduce((total, item) => total + (item.criteria || []).length, 0),
      version: template.version, status: template.status, updatedAt: template.updatedAt };
  }

  function changed(template, kind) {
    ctx.invalidateTemplates();
    app.api.invalidate(`/v1/admin/evaluation-templates/${template.id}`);
    const visible = status === 'all' || status === template.status;
    if (kind === 'created') {
      if (visible) list.reload();
    } else if (visible) {
      list.replace((row) => row.id === template.id, summaryOf(template));
    } else {
      list.remove((row) => row.id === template.id);
    }
    ctx.emit({ type: 'template', kind, template });
  }

  async function createTemplate(source = null) {
    const saved = await templateDialog(app, source, { copy: Boolean(source) });
    if (!saved) return;
    app.toast(source ? 'Şablonun kopyası oluşturuldu.' : 'Şablon oluşturuldu.', 'good');
    changed(saved, 'created');
    openTemplate(saved.id, saved);
  }

  function openTemplate(id, initial = null) {
    const panel = openDrawer(app, { title: initial?.name || 'Değerlendirme şablonu' });
    let first = initial;
    const view = loader(async () => {
      if (first) { const value = first; first = null; return value; }
      return app.api.getCached(`/v1/admin/evaluation-templates/${id}`, undefined, { ttlMs: 30_000 });
    }, (template) => {
      panel.setTitle(template.name);
      const nodes = [];
      if (template.status === 'archived') {
        nodes.push(alertBox('Bu şablon arşivde: yeni döneme seçilemez. Geçmiş değerlendirmeler ve '
          + 'gelişim grafiği etkilenmez.', 'warn'));
      }
      nodes.push(card('', kv([
        ['Ölçek', scaleText(template.scale)],
        ['Sürüm', `v${template.version}`],
        ['Durum', statusBadge(TEMPLATE_STATUS, template.status)],
        ['Kullanan dönem', template.periodCount ? `${num(template.periodCount)} dönem` : 'Henüz kullanılmadı'],
        ['Serbest alanlar', [template.includeStrengths && 'Güçlü yönler',
          template.includeImprovements && 'Gelişim alanları'].filter(Boolean).join(' · ') || 'Yok'],
        ['Açıklama', template.description || '—'],
        ['Güncellendi', fmtDateTime(template.updatedAt || template.createdAt)],
      ])));
      for (const sectionItem of [...(template.sections || [])].sort((a, b) => a.order - b.order)) {
        const rows = h('div', 'ak-ev-tpl-criteria');
        for (const criterion of [...(sectionItem.criteria || [])].sort((a, b) => a.order - b.order)) {
          const row = h('div', 'ak-ev-tpl-criterion');
          const text = h('div', 'ak-cell-stack');
          text.append(h('b', '', criterion.title));
          if (criterion.description) text.append(h('small', '', criterion.description));
          row.append(text);
          if (criterion.autoMetric) {
            row.append(h('span', 'kit-badge info', `Otomatik: ${AUTO_METRICS[criterion.autoMetric] || criterion.autoMetric}`));
          }
          rows.append(row);
        }
        nodes.push(card(sectionItem.title, rows, `${num((sectionItem.criteria || []).length)} kriter`));
      }
      const tools = h('div', 'ak-inline');
      if (template.status === 'active') {
        tools.append(actionButton(app, 'admin_updateEvaluationTemplate', 'Düzenle', { variant: 'primary',
          onClick: async () => {
            const saved = await templateDialog(app, template);
            if (!saved) return;
            app.toast(`Şablon güncellendi (v${saved.version}). Mevcut değerlendirmeler eski sürümle kalır.`, 'good');
            changed(saved, 'updated');
            first = saved;
            view.reload();
          } }));
      }
      tools.append(actionButton(app, 'admin_createEvaluationTemplate', 'Kopyasını oluştur', {
        onClick: () => { panel.close(); createTemplate(template); } }));
      if (template.status === 'active') {
        tools.append(actionButton(app, 'admin_archiveEvaluationTemplate', 'Arşivle', { variant: 'danger',
          onClick: async (event) => {
            const control = event.currentTarget;
            const ok = await confirmAction(app, { title: `“${template.name}” arşivlensin mi?`,
              description: 'Şablon silinmez; yeni döneme seçilemez. Açık dönemler ve geçmiş değerlendirmeler '
                + 'etkilenmez.', confirmLabel: 'Arşivle', danger: true });
            if (!ok) return;
            const done = await runAction(app, async () => {
              await app.api.del(`/v1/admin/evaluation-templates/${template.id}`);
              return true;
            }, { success: 'Şablon arşivlendi.', button: control });
            if (done) {
              const archived = { ...template, status: 'archived' };
              changed(archived, 'archived');
              first = archived;
              view.reload();
            }
          } }));
      }
      nodes.push(tools);
      return nodes;
    });
    panel.node.classList.add('ak-ev-drawer-narrow');
    panel.body.append(view.node);
    view.reload();
  }

  let started = false;
  return {
    node,
    reload: () => list.reload(),
    show() { if (!started) { started = true; list.reload(); } },
    open: (id) => openTemplate(id),
    destroy() { list.destroy(); },
  };
}

// ------------------------------------------------------------ düzenleyici

/** Şablon penceresi: yeni, düzenle ya da kopya. Kaydedilen şablonu döndürür. */
export function templateDialog(app, template = null, { copy = false } = {}) {
  const editing = Boolean(template) && !copy;
  const inUse = editing && (template.periodCount || 0) > 0;
  const sections = template
    ? [...(template.sections || [])].sort((a, b) => a.order - b.order).map((item) => ({
      id: copy ? null : item.id,
      title: item.title,
      criteria: [...(item.criteria || [])].sort((a, b) => a.order - b.order).map((criterion) => ({
        id: copy ? null : criterion.id, title: criterion.title, description: criterion.description || '',
        autoMetric: criterion.autoMetric || '' })),
    }))
    : [];
  return formDialog(app, {
    title: editing ? `Şablonu düzenle: ${template.name}` : copy ? 'Şablon kopyası' : 'Yeni değerlendirme şablonu',
    description: editing
      ? 'Kaydedince sürüm artar. Mevcut değerlendirmeler kendi kopyalarını korur; açık dönemde yeni açılan '
        + 'değerlendirmeler yeni sürümü kullanır.'
      : 'Bölümleri ve kriterleri sırayla yazın. Kritere bağlanan otomatik veri, formun yan panelinde koça '
        + 'yol gösterir.',
    wide: true,
    value: {
      name: template ? (copy ? `${template.name} (kopya)`.slice(0, 80) : template.name) : '',
      description: template?.description || '',
      scaleKind: template?.scale?.kind || 'numeric_5',
      includeStrengths: template ? Boolean(template.includeStrengths) : true,
      includeImprovements: template ? Boolean(template.includeImprovements) : true,
      sections,
    },
    fields: [
      { key: 'name', label: 'Şablon adı', required: true, maxLength: 80,
        placeholder: 'ör. Dönem sonu gelişim değerlendirmesi',
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter.' : null) },
      { key: 'scaleKind', label: 'Ölçek', type: 'select', readOnly: inUse,
        options: Object.entries(SCALE_KINDS).map(([value, label]) => ({ value, label })),
        hint: inUse ? 'Dönemde kullanılmış şablonun ölçeği değişmez.' : '' },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500, wide: true },
      { key: 'includeStrengths', label: 'Güçlü yönler alanı', type: 'checkbox' },
      { key: 'includeImprovements', label: 'Gelişim alanları alanı', type: 'checkbox' },
      { key: 'sections', label: 'Bölümler ve kriterler', type: 'custom', required: true,
        render: (draft, set) => sectionsEditor(draft.sections || [], set, { allowPreset: !template }),
        validate: (value) => sectionsProblem(value) },
    ],
    submitLabel: editing ? 'Kaydet (yeni sürüm)' : 'Şablonu oluştur',
    onSubmit: (draft, { key }) => {
      const body = {
        name: String(draft.name || '').trim(),
        description: String(draft.description || '').trim() || null,
        scaleKind: draft.scaleKind || 'numeric_5',
        sections: (draft.sections || []).map((item) => ({
          id: item.id || null,
          title: item.title.trim(),
          criteria: item.criteria.map((criterion) => ({
            id: criterion.id || null,
            title: criterion.title.trim(),
            description: String(criterion.description || '').trim() || null,
            autoMetric: criterion.autoMetric || null,
          })),
        })),
        includeStrengths: Boolean(draft.includeStrengths),
        includeImprovements: Boolean(draft.includeImprovements),
      };
      return editing
        ? app.api.put(`/v1/admin/evaluation-templates/${template.id}`, body)
        : app.api.post('/v1/admin/evaluation-templates', body, { key });
    },
  });
}

function sectionsProblem(sections) {
  if (!Array.isArray(sections) || !sections.length) return 'En az bir bölüm ekleyin.';
  for (const [index, item] of sections.entries()) {
    const name = `${index + 1}. bölüm`;
    const title = String(item.title || '').trim();
    if (title.length < 2 || title.length > 60) return `${name}: başlık 2–60 karakter olmalı.`;
    if (!item.criteria.length) return `${name}: en az bir kriter ekleyin.`;
    for (const [position, criterion] of item.criteria.entries()) {
      const text = String(criterion.title || '').trim();
      if (text.length < 2 || text.length > 80) {
        return `${name}, ${position + 1}. kriter: ad 2–80 karakter olmalı.`;
      }
      if (String(criterion.description || '').length > 300) {
        return `${name}, ${position + 1}. kriter: açıklama en çok 300 karakter.`;
      }
    }
  }
  return null;
}

/**
 * Bölüm → kriter düzenleyicisi. Yazı alanları durumu yerinde değiştirir
 * (odak kaybolmasın diye yeniden çizilmez); yalnız ekleme / silme / taşıma
 * yeniden çizer.
 */
function sectionsEditor(initial, set, { allowPreset = false } = {}) {
  const sections = initial.map((item) => ({ ...item, criteria: item.criteria.map((criterion) => ({ ...criterion })) }));
  const node = h('div', 'ak-ev-editor');
  const commit = () => set(sections);

  function input(value, placeholder, maxLength, onInput, className = 'kit-input') {
    const field = h('input', className);
    field.type = 'text';
    field.value = value || '';
    field.placeholder = placeholder;
    field.maxLength = maxLength;
    field.addEventListener('input', () => { onInput(field.value); commit(); });
    return field;
  }

  function move(list, index, step) {
    const target = index + step;
    if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    paint();
  }

  function paint() {
    node.replaceChildren();
    sections.forEach((item, index) => {
      const box = h('div', 'ak-ev-sec');
      const head = h('div', 'ak-ev-sec-head');
      head.append(h('span', 'ak-ev-sec-no', `${index + 1}.`),
        input(item.title, 'Bölüm başlığı (ör. Akademik gelişim)', 60, (value) => { item.title = value; },
          'kit-input ak-ev-sec-title'),
        iconButton('↑', 'Bölümü yukarı taşı', () => move(sections, index, -1), index === 0),
        iconButton('↓', 'Bölümü aşağı taşı', () => move(sections, index, 1), index === sections.length - 1),
        iconButton('✕', 'Bölümü kaldır', () => { sections.splice(index, 1); paint(); }));
      box.append(head);
      const list = h('div', 'ak-ev-crit-list');
      if (item.criteria.length) {
        const legend = h('div', 'ak-ev-crit-row ak-ev-crit-legend');
        legend.append(h('span', '', 'Kriter'), h('span', '', 'Açıklama (formda koça görünür)'),
          h('span', '', 'Otomatik veri'), h('span'));
        list.append(legend);
      }
      item.criteria.forEach((criterion, position) => {
        const row = h('div', 'ak-ev-crit-row');
        const metric = h('select', 'kit-select');
        metric.setAttribute('aria-label', 'Otomatik veri');
        for (const option of metricOptions) {
          const entry = h('option', '', option.label);
          entry.value = option.value;
          metric.append(entry);
        }
        metric.value = criterion.autoMetric || '';
        metric.addEventListener('change', () => { criterion.autoMetric = metric.value; commit(); });
        const tools = h('div', 'ak-inline ak-ev-crit-tools');
        tools.append(
          iconButton('↑', 'Kriteri yukarı taşı', () => move(item.criteria, position, -1), position === 0),
          iconButton('↓', 'Kriteri aşağı taşı', () => move(item.criteria, position, 1),
            position === item.criteria.length - 1),
          iconButton('✕', 'Kriteri kaldır', () => { item.criteria.splice(position, 1); paint(); }),
        );
        row.append(
          input(criterion.title, 'Kriter adı', 80, (value) => { criterion.title = value; }),
          input(criterion.description, 'İsteğe bağlı açıklama', 300, (value) => { criterion.description = value; }),
          metric, tools);
        list.append(row);
      });
      box.append(list);
      box.append(button('+ Kriter ekle', { variant: 'ghost', onClick: () => {
        item.criteria.push({ id: null, title: '', description: '', autoMetric: '' });
        paint();
        node.querySelectorAll('.ak-ev-sec')[index]?.querySelector('.ak-ev-crit-row:last-child input')?.focus();
      } }));
      node.append(box);
    });
    const foot = h('div', 'ak-inline');
    foot.append(button('+ Bölüm ekle', { onClick: () => {
      sections.push({ id: null, title: '', criteria: [{ id: null, title: '', description: '', autoMetric: '' }] });
      paint();
      [...node.querySelectorAll('.ak-ev-sec-title')].pop()?.focus();
    } }));
    if (allowPreset && !sections.length) {
      foot.append(button('Önerilen bölümlerle başla', { variant: 'ghost', onClick: () => {
        for (const preset of PRESET_SECTIONS) {
          sections.push({ id: null, title: preset.title,
            criteria: preset.criteria.map((criterion) => ({ id: null, ...criterion,
              autoMetric: criterion.autoMetric || '' })) });
        }
        paint();
      } }));
    }
    const total = sections.reduce((count, item) => count + item.criteria.length, 0);
    foot.append(h('span', 'ak-muted', total ? `${num(sections.length)} bölüm · ${num(total)} kriter` : ''));
    node.append(foot);
    commit();
  }

  paint();
  return node;
}
