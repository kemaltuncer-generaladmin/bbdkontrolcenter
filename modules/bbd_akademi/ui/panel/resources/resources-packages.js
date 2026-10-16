// BBD Akademi — Kaynaklar › Paketler (F-C5).
//
// Paket = kaynak listesi + otomatik atama kuralları. Kural sınıf seviyesi /
// alan / hedef sınav üçlüsüdür ve KM'deki öğrenci kaydından gelen bilgiyle
// eşleşir (kullanıcı kararı: hazır kaynak paketleri öğrencinin KM'de tanımlı
// seviye/alan/hedefinden çıkar). Kuraldaki dolu koşulların HEPSİ aranır;
// kurallardan HERHANGİ birine uyan öğrenci pakete girer. Otomatik atama
// yalnız YENİ öğrencilere (dizin senkronunda) işler; var olan öğrenciler için
// "Şimdi uygula" (kural / sınıf / grup / öğrenci; önce `dryRun` ile sayılır).
// Paket silinince yapılmış atamalar KALIR.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { AkademiError } from '../api.js';
import {
  actionButton, confirmAction, empty, errorBox, fmtDate, fmtDateTime, formDialog, kv, loader, num,
  openDrawer, optionsOf, runAction, todayIst, toolbar,
} from '../common.js';
import {
  EXAM_TARGETS, GRADE_OPTIONS, KINDS, TRACKS, contentText, gradeValue, levelText, loadTargets,
  resourceCell, resourceListField, ruleText, scopeBadge, stackCell, targetsField,
} from './resources-common.js';

const APPLY_SCOPES = { rule_matches: 'Kurala uyan bütün öğrenciler', class_groups: 'Sınıflar',
  student_groups: 'Çalışma grupları', students: 'Seçili öğrenciler' };

export function packagesTab(app, ctx) {
  const node = h('div', 'ak-stack');
  const add = actionButton(app, 'admin_createResourcePackage', 'Yeni paket', { variant: 'primary',
    onClick: async () => {
      const saved = await packageForm(app, null);
      if (saved) {
        app.toast(saved.autoAssign?.enabled
          ? `“${saved.name}” oluşturuldu. Var olan öğrencilere atamak için “Şimdi uygula”yı kullanın.`
          : `“${saved.name}” oluşturuldu.`, 'good');
        ctx.changed('packages');
      }
    } });
  node.append(toolbar(h('span', 'ak-muted', 'Örn. “12 SAY Temel Paket”: kurala uyan yeni öğrenciye kendiliğinden atanır.'),
    h('span', 'kit-spacer'), add));

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Paket', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const title = h('button', 'ak-res-link', row.name);
        title.type = 'button';
        title.addEventListener('click', () => ctx.openPackage(row.id));
        cell.append(title);
        if (row.description) cell.append(h('small', '', row.description));
        return cell;
      } },
      { key: 'resources', label: 'Kaynaklar', width: 'minmax(0, 2fr)', cell: (row) => stackCell(
        `${num((row.resources || []).length)} kaynak`,
        (row.resources || []).slice(0, 3).map((item) => item.title).join(', ')
          + ((row.resources || []).length > 3 ? ' …' : '')) },
      { key: 'auto', label: 'Otomatik atama', width: 'minmax(0, 1.6fr)', cell: (row) => autoCell(row.autoAssign) },
      { key: 'students', label: 'Atanan öğrenci', width: '110px', align: 'num', cell: (row) => num(row.assignedStudentCount) },
      { key: 'created', label: 'Oluşturma', width: '100px', cell: (row) => fmtDate(row.createdAt) },
      { key: 'actions', label: '', width: '230px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(
          actionButton(app, 'admin_applyResourcePackage', 'Şimdi uygula', { variant: 'ghost',
            onClick: () => applyPackage(app, row, ctx) }),
          actionButton(app, 'admin_updateResourcePackage', 'Düzenle', { variant: 'ghost',
            onClick: () => editPackage(app, row, ctx) }),
          actionButton(app, 'admin_deleteResourcePackage', 'Sil', { variant: 'ghost',
            onClick: () => deletePackage(app, row, ctx) }),
        );
        return actions;
      } },
    ],
    rows: [],
    empty: empty('Paket yok', 'Seviye ve alana göre hazır kaynak setleri oluşturun; yeni öğrencilere kendiliğinden atansın.'),
  });
  const view = loader(() => app.api.getCached('/v1/admin/resource-packages'), (data) => {
    table.update({ rows: data?.items || [] });
    return table.node;
  });
  node.append(view.node);

  return { node, reload: () => view.reload() };
}

function autoCell(autoAssign) {
  const cell = h('div', 'ak-cell-stack');
  const rules = autoAssign?.rules || [];
  cell.append(autoAssign?.enabled ? badge(`Açık · ${num(rules.length)} kural`, 'good') : badge('Kapalı', 'dim'));
  if (rules.length) cell.append(h('small', '', rules.map(ruleText).join(' | ')));
  return cell;
}

// ------------------------------------------------------------------ çekmece

export function packageDrawer(app, packageId, ctx) {
  const panel = openDrawer(app, { title: 'Kaynak paketi', subtitle: 'Yükleniyor…' });
  panel.node.classList.add('ak-res-drawer');
  const view = loader(() => app.api.get(`/v1/admin/resource-packages/${packageId}`), (item) => {
    panel.setTitle(item.name);
    const tools = toolbar(
      actionButton(app, 'admin_applyResourcePackage', 'Şimdi uygula', { variant: 'primary',
        onClick: async () => { if (await applyPackage(app, item, ctx)) view.reload(); } }),
      actionButton(app, 'admin_updateResourcePackage', 'Düzenle', {
        onClick: async () => { if (await editPackage(app, item, ctx)) view.reload(); } }),
      button('Atamaları', { variant: 'ghost', onClick: () => {
        panel.close();
        ctx.showAssignments({ packageId: item.id });
      } }),
      button('İlerleme', { variant: 'ghost', onClick: () => {
        panel.close();
        ctx.showProgress({ packageId: item.id });
      } }),
      h('span', 'kit-spacer'),
      actionButton(app, 'admin_deleteResourcePackage', 'Sil', { variant: 'ghost',
        onClick: async () => { if (await deletePackage(app, item, ctx)) panel.close(); } }),
    );
    const rules = item.autoAssign?.rules || [];
    const info = card('', kv([
      ['Açıklama', item.description],
      ['Otomatik atama', item.autoAssign?.enabled
        ? 'Açık — kurala uyan yeni öğrencilere dizin senkronunda atanır' : 'Kapalı'],
      ['Kurallar', rules.length ? rules.map(ruleText).join(' | ') : 'Kural yok'],
      ['Atanan öğrenci', num(item.assignedStudentCount)],
      ['Oluşturma', fmtDateTime(item.createdAt)],
    ]));
    const resources = dataTable({
      columns: [
        { key: 'title', label: 'Kaynak', width: 'minmax(0, 2.2fr)',
          cell: (row) => resourceCell(row, (resource) => ctx.openResource(resource.id)) },
        { key: 'kind', label: 'Tür', width: '120px', cell: (row) => KINDS[row.kind] || row.kind },
        { key: 'level', label: 'Seviye / sınav', width: 'minmax(0, 1.2fr)', cell: (row) => levelText(row) },
        { key: 'content', label: 'İçerik', width: '150px', cell: (row) => contentText(row) },
        { key: 'scope', label: 'Kapsam', width: '120px', cell: (row) => scopeBadge(row.scope) },
      ],
      rows: item.resources || [],
      empty: empty('Pakette kaynak yok', '“Düzenle” ile kaynak ekleyin.'),
    });
    return [tools, info, card('Kaynaklar', resources.node, `${num((item.resources || []).length)} kaynak`)];
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();
  return panel;
}

// ------------------------------------------------------------------ eylemler

async function editPackage(app, item, ctx) {
  // Liste yükü kaynakları zaten taşır; ayrıntı yalnız eksikse çekilir.
  let full = item;
  if (!Array.isArray(item.resources)) {
    full = await runAction(app, () => app.api.get(`/v1/admin/resource-packages/${item.id}`));
    if (!full) return null;
  }
  const saved = await packageForm(app, full);
  if (saved) {
    app.toast('Paket kaydedildi.', 'good');
    ctx.changed('packages');
  }
  return saved;
}

async function deletePackage(app, item, ctx) {
  const yes = await confirmAction(app, {
    title: `“${item.name}” paketi silinsin mi?`,
    description: `Paketle yapılmış atamalar KALIR (${num(item.assignedStudentCount)} öğrenci kaynaklarını görmeye devam eder); `
      + 'yalnız paket ve otomatik atama kuralları kalkar. Atamayı kaldırmak için Atamalar sekmesini kullanın.',
    confirmLabel: 'Paketi sil',
    danger: true,
  });
  if (!yes) return false;
  const done = await runAction(app, () => app.api.del(`/v1/admin/resource-packages/${item.id}`),
    { success: 'Paket silindi; atamalar yerinde.' });
  if (done === undefined) return false;
  ctx.changed('packages');
  return true;
}

/**
 * Kural satırları: seviye / alan / hedef sınav (boş = koşul yok). `rules`
 * çağıranın dizisidir ve yerinde düzenlenir; `onChange` her değişiklikte çağrılır.
 */
export function rulesField(rules, onChange) {
  const wrap = h('div', 'ak-stack');
  const list = h('div', 'ak-res-rules');
  const publish = () => onChange(rules.map((rule) => ({ ...rule })));
  const select = (options, value, onChange, label) => {
    const node = h('select', 'kit-select');
    node.setAttribute('aria-label', label);
    for (const option of options) {
      const item = h('option', '', option.label);
      item.value = option.value;
      node.append(item);
    }
    node.value = value;
    node.addEventListener('change', () => onChange(node.value));
    return node;
  };
  const paint = () => {
    list.replaceChildren();
    if (!rules.length) list.append(h('div', 'ak-muted', 'Kural yok. Otomatik atama için en az bir kural ekleyin.'));
    rules.forEach((rule, index) => {
      const row = h('div', 'ak-res-rule');
      row.append(
        h('span', 'ak-res-rule-no', `${index + 1}.`),
        select([{ value: '', label: 'Her seviye' }, ...GRADE_OPTIONS], rule.gradeLevel,
          (value) => { rule.gradeLevel = value; publish(); }, 'Sınıf seviyesi'),
        select(optionsOf(TRACKS, { empty: 'Her alan' }), rule.track,
          (value) => { rule.track = value; publish(); }, 'Alan'),
        select(optionsOf(EXAM_TARGETS, { empty: 'Her hedef sınav' }), rule.examTarget,
          (value) => { rule.examTarget = value; publish(); }, 'Hedef sınav'),
        button('Kuralı sil', { variant: 'ghost', onClick: () => { rules.splice(index, 1); paint(); publish(); } }),
      );
      list.append(row);
    });
  };
  paint();
  wrap.append(list, button('+ Kural ekle', { variant: 'ghost', onClick: () => {
    rules.push({ gradeLevel: '', track: '', examTarget: '' });
    paint();
    publish();
  } }));
  return wrap;
}

/**
 * Paket oluştur / düzenle.
 * @returns {Promise<object|null>}
 */
async function packageForm(app, current) {
  // Özel alan değerleri kapanışta (formDialog `value`su formGrid'e de gider ve
  // ızgara değişikliğinde özel alanların ilk değerini geri yazar).
  let resources = [...(current?.resources || [])];
  const rules = (current?.autoAssign?.rules || []).map((rule) => ({
    gradeLevel: rule.gradeLevel === null || rule.gradeLevel === undefined ? '' : String(rule.gradeLevel),
    track: rule.track || '',
    examTarget: rule.examTarget || '',
  }));
  const saved = await formDialog(app, {
    title: current ? `“${current.name}” — düzenle` : 'Yeni kaynak paketi',
    description: 'Kurallar KM’deki öğrenci kaydından gelen sınıf seviyesi, alan ve hedef sınava bakar. '
      + 'Bir kuraldaki dolu koşulların hepsi aranır; kurallardan herhangi birine uyan öğrenci pakete girer.',
    wide: true,
    value: {
      name: current?.name || '',
      description: current?.description || '',
      autoEnabled: Boolean(current?.autoAssign?.enabled),
    },
    fields: [
      { key: 'name', label: 'Paket adı', required: true, maxLength: 80, placeholder: '12 SAY Temel Paket' },
      { key: 'description', label: 'Açıklama', maxLength: 300 },
      { key: 'resourceIds', label: 'Kaynaklar', type: 'custom', render: () => resourceListField(app, {
        initial: resources, onChange: (list) => { resources = list; } }).node,
      validate: () => (resources.length ? null : 'Pakete en az bir kaynak ekleyin.') },
      { key: 'autoEnabled', label: 'Kurala uyan yeni öğrencilere otomatik ata', type: 'checkbox' },
      { key: 'rules', label: 'Otomatik atama kuralları', type: 'custom', render: () => rulesField(rules, () => {}),
        hint: 'Örn. “12. sınıf · Sayısal · YKS hedefi”. Boş bırakılan koşul her değeri kabul eder.',
        validate: (_raw, draft) => {
          if (rules.some((rule) => !rule.gradeLevel && !rule.track && !rule.examTarget)) {
            return 'Her kuralda en az bir koşul seçin (boş kural bütün öğrencileri kapsar).';
          }
          if (draft.autoEnabled && !rules.length) return 'Otomatik atama açıkken en az bir kural gerekir.';
          return null;
        } },
    ],
    submitLabel: current ? 'Kaydet' : 'Paketi oluştur',
    onSubmit: (draft, { key }) => {
      const body = {
        name: String(draft.name || '').trim(),
        description: String(draft.description || '').trim() || null,
        resourceIds: resources.map((item) => item.id),
        autoAssign: {
          enabled: Boolean(draft.autoEnabled),
          rules: rules.map((rule) => ({
            gradeLevel: gradeValue(rule.gradeLevel),
            track: rule.track || null,
            examTarget: rule.examTarget || null,
          })),
        },
      };
      if (!body.resourceIds.length) throw new AkademiError({ message: 'Pakete en az bir kaynak ekleyin.' });
      return current ? app.api.put(`/v1/admin/resource-packages/${current.id}`, body)
        : app.api.post('/v1/admin/resource-packages', body, { key });
    },
  });
  return saved && saved !== true ? saved : null;
}

/**
 * Şimdi uygula: kapsam seç → önce say (`dryRun`) → uygula. Zaten atanmış
 * kaynaklar Akademi'de tekrar atanmaz (sonuçta ayrıca sayılır).
 * @returns {Promise<object|null>}
 */
export async function applyPackage(app, item, ctx) {
  const [classes, groups] = await loadTargets(app);
  const hasRules = (item.autoAssign?.rules || []).length > 0;
  const scopes = Object.entries(APPLY_SCOPES)
    .filter(([key]) => key !== 'rule_matches' || hasRules)
    .map(([value, label]) => ({ value, label }));
  const previewSlot = h('div', 'ak-res-gen-preview');
  previewSlot.append(h('div', 'ak-muted', '“Önce say” atama yapmadan kaç öğrenciye kaç yeni atama düşeceğini gösterir.'));

  const body = (draft) => ({
    scope: draft.scope,
    classGroupIds: draft.scope === 'class_groups' ? draft.targets?.classGroupIds || [] : [],
    studentGroupIds: draft.scope === 'student_groups' ? draft.targets?.studentGroupIds || [] : [],
    studentIds: draft.scope === 'students' ? draft.targets?.studentIds || [] : [],
    targetDate: draft.targetDate || null,
  });
  const missing = (draft) => {
    const value = body(draft);
    if (value.scope === 'class_groups' && !value.classGroupIds.length) return 'En az bir sınıf seçin.';
    if (value.scope === 'student_groups' && !value.studentGroupIds.length) return 'En az bir çalışma grubu seçin.';
    if (value.scope === 'students' && !value.studentIds.length) return 'En az bir öğrenci seçin.';
    return null;
  };
  const counts = (result) => kpiRow([
    { label: 'Öğrenci', value: num(result.studentCount) },
    { label: 'Yeni atama', value: num(result.createdAssignments), tone: result.createdAssignments ? 'good' : '' },
    { label: 'Zaten atanmış', value: num(result.alreadyAssigned) },
  ]);

  // Kapsama göre tek hedef kutusu (sınıf / grup / öğrenci); kapsam değişince yeniden çizilir.
  const targetPicker = (draft, set) => {
    const wrap = h('div');
    let shown = null;
    const paint = () => {
      if (draft.scope === shown) return;
      shown = draft.scope;
      wrap.replaceChildren();
      if (draft.scope === 'rule_matches') {
        wrap.append(h('span', 'ak-muted', `Kurallar: ${(item.autoAssign?.rules || []).map(ruleText).join(' | ')}. `
          + 'KM’deki seviye/alan/hedef bilgisi uyan bütün aktif öğrenciler.'));
        set({});
        return;
      }
      const kind = { class_groups: 'classes', student_groups: 'groups', students: 'students' }[draft.scope];
      const field = targetsField(app, { classes, groups, kinds: [kind], onChange: (value) => set(value) });
      wrap.append(field.node);
      set(field.value());
    };
    paint();
    return { node: wrap, update: paint };
  };

  const result = await formDialog(app, {
    title: `“${item.name}” — şimdi uygula`,
    description: `${num((item.resources || []).length)} kaynak seçilen öğrencilere atanır. Dönem başı toplu atama içindir; `
      + 'daha önce atanmış kaynaklar tekrar atanmaz.',
    wide: true,
    value: { scope: hasRules ? 'rule_matches' : 'class_groups', targetDate: '' },
    fields: [
      { key: 'scope', label: 'Kime', type: 'select', options: scopes },
      { key: 'targetDate', label: 'Hedef bitiş günü (isteğe bağlı)', type: 'date' },
      { key: 'targets', label: 'Hedef', type: 'custom', render: targetPicker, wide: true,
        validate: (_raw, draft) => missing(draft) },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önce say', { onClick: async () => {
          const reason = missing(draft);
          if (reason) { previewSlot.replaceChildren(alertBox(reason, 'warn')); return; }
          go.disabled = true;
          try {
            const preview = await app.api.post(`/v1/admin/resource-packages/${item.id}/apply`,
              { ...body(draft), dryRun: true });
            previewSlot.replaceChildren(h('div', 'ak-muted', 'Önizleme — henüz atama yapılmadı:'), counts(preview));
          } catch (failure) {
            previewSlot.replaceChildren(errorBox(failure));
          } finally {
            go.disabled = false;
          }
        } });
        wrap.append(go, previewSlot);
        return wrap;
      } },
    ],
    submitLabel: 'Uygula',
    onSubmit: (draft, { key }) => {
      if (draft.targetDate && draft.targetDate < todayIst()) {
        throw new AkademiError({ message: 'Hedef bitiş günü geçmişte olamaz.' });
      }
      return app.api.post(`/v1/admin/resource-packages/${item.id}/apply`, { ...body(draft), dryRun: false }, { key });
    },
  });
  if (!result || result === true) return null;
  app.toast(`${num(result.createdAssignments)} yeni atama yapıldı · ${num(result.alreadyAssigned)} zaten atanmıştı `
    + `(${num(result.studentCount)} öğrenci).`, 'good');
  ctx.changed('packages');
  return result;
}
