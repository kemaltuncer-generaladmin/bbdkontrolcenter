// BBD Akademi — Çalışma planı şablonları (F-A18, yalnız KM).
//
// Şablon = gün ofsetli görev listesi ("12 SAY Ekim haftası", "LGS son 30 gün").
// Uygulamak `dayOffset`'leri başlangıç gününe ekleyip `templateId` dolu bir
// plan ataması açar; sonrası Atamalar sekmesinden yönetilir. Şablonu
// güncellemek `version`'ı artırır, önceden uygulanmış atamalar DEĞİŞMEZ.
//
// Şablon listesi ve ayrıntısı ETag'lidir (seyrek değişir): ayrıntı
// `getCached` ile okunur, her yazmadan sonra `/v1/admin/plan/templates`
// önbelleği düşürülür.
//
// Plan atamasının çekmecesi plan.js'tedir; burası ona `ctx.onApplied` ile
// haber verir (döngüsel import yok).

import { h } from '../../ui-kit/kit.js';
import { alertBox, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import {
  EXAM_TARGETS, GRADE_LEVELS, TRACKS, actionButton, addDays, confirmAction, empty, fmtDate,
  fmtDateTime, formDialog, gradeText, kv, loader, multiChips, num, openDrawer, optionsOf, pagedList,
  problem, runAction, selectBox, todayIst, toolbar,
} from './common.js';
import {
  HOLIDAY_POLICIES, TASK_TYPES, detailText, loadTargets, sortTasks, subjectMap, targetsField,
  taskBody, taskListField, taskTitle, timeText, totalMinutes, whenText,
} from './plan-tasks.js';

const GRADE_OPTIONS = GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }));

/** '11' → 11, 'graduate' → 'graduate' (sözleşme: 5..12 | 'graduate'). */
function gradeValue(raw) {
  return raw === 'graduate' ? 'graduate' : Number(raw);
}

/** Seviye · alan · hedef tek satırda. */
export function levelText(template) {
  const grades = (template?.gradeLevels || []).map((level) => gradeText(level)).join(', ');
  const tracks = (template?.tracks || []).map((track) => TRACKS[track] || track).join(', ');
  return [grades, tracks, template?.examTarget ? `${EXAM_TARGETS[template.examTarget] || template.examTarget} hedefi` : '']
    .filter(Boolean).join(' · ') || 'Bütün seviyeler';
}

// --------------------------------------------------------------- sekme

export function templatesTab(app, ctx) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_listStudyPlanTemplates')) {
    node.append(empty('Şablon yönetimi bu hesaba açık değil',
      'Çalışma planı şablonları yalnız Kontrol Merkezi\'nden, yetkili kullanıcılarca yönetilir. '
      + 'Yöneticinizden izin isteyin.'));
    return { node, reload() {}, destroy() {} };
  }
  const filters = { gradeLevel: '', examTarget: '' };
  const gradeSelect = selectBox([{ value: '', label: 'Tüm seviyeler' }, ...GRADE_OPTIONS], '', (value) => {
    filters.gradeLevel = value; list.reload();
  }, { label: 'Seviye' });
  const targetSelect = selectBox(optionsOf(EXAM_TARGETS, { empty: 'Tüm hedefler' }), '', (value) => {
    filters.examTarget = value; list.reload();
  }, { label: 'Hedef sınav' });
  const add = actionButton(app, 'admin_createStudyPlanTemplate', 'Yeni şablon', { variant: 'primary',
    onClick: () => edit(null) });
  node.append(toolbar(gradeSelect.node, targetSelect.node, h('span', 'kit-spacer'),
    h('span', 'ak-muted', 'Gün sırasıyla hazır görev listeleri; sınıfa, gruba ya da öğrenciye uygulanır.'), add));

  // Satırda eylem düğmeleri var: satır DÜĞME olmaz (iç içe düğme), ad bağlantıdır.
  const list = pagedList({
    app,
    path: '/v1/admin/plan/templates',
    query: () => filters,
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2.2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const link = h('button', 'ak-plan-link', row.name);
        link.type = 'button';
        link.addEventListener('click', () => open(row.id));
        cell.append(link, h('small', '', levelText(row)));
        return cell;
      } },
      { key: 'duration', label: 'Süre', width: '80px', align: 'num', cell: (row) => `${num(row.durationDays)} gün` },
      { key: 'tasks', label: 'Görev', width: '70px', align: 'num', cell: (row) => num(row.taskCount) },
      { key: 'updated', label: 'Güncelleme', width: '130px', cell: (row) => fmtDateTime(row.updatedAt) },
      { key: 'actions', label: '', width: '220px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(
          actionButton(app, 'admin_applyStudyPlanTemplate', 'Uygula', { variant: 'primary',
            onClick: () => applyTemplate(app, row, ctx).then((done) => { if (done) list.reload(); }) }),
          actionButton(app, 'admin_updateStudyPlanTemplate', 'Düzenle', { variant: 'ghost',
            onClick: () => edit(row) }),
          actionButton(app, 'admin_deleteStudyPlanTemplate', 'Sil', { variant: 'ghost',
            onClick: () => remove(row) }),
        );
        return actions;
      } },
    ],
    empty: { title: 'Şablon yok', text: 'Örneğin “LGS son 30 gün” şablonunu oluşturun; sonra sınıfa tek adımda uygulayın.' },
  });
  node.append(list.node);

  function changed() {
    app.api.invalidate('/v1/admin/plan/templates');
    list.reload();
  }

  function open(id) {
    templateDrawer(app, id, { ctx, onChange: changed, onEdit: (template) => edit(template),
      onRemove: (template) => remove(template) });
  }

  async function edit(row) {
    let template = null;
    if (row) {
      template = await runAction(app, () => app.api.getCached(`/v1/admin/plan/templates/${row.id}`));
      if (!template) return;
    }
    const saved = await editTemplate(app, template);
    if (saved) {
      app.toast(row ? `“${saved.name}” güncellendi (sürüm ${num(saved.version)}).` : `“${saved.name}” oluşturuldu.`, 'good');
      changed();
    }
  }

  async function remove(row) {
    const yes = await confirmAction(app, { title: `“${row.name}” şablonu silinsin mi?`,
      description: 'Bu şablonla önceden açılmış plan atamaları etkilenmez.', confirmLabel: 'Sil', danger: true });
    if (!yes) return false;
    const done = await runAction(app, async () => {
      await app.api.del(`/v1/admin/plan/templates/${row.id}`);
      return true;
    }, { success: 'Şablon silindi.' });
    if (done) changed();
    return Boolean(done);
  }

  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

// --------------------------------------------------------------- ayrıntı

function templateDrawer(app, id, { ctx, onChange, onEdit, onRemove }) {
  const panel = openDrawer(app, { title: 'Çalışma planı şablonu', subtitle: 'Gün sırasıyla görev listesi' });
  const view = loader(() => Promise.all([app.api.getCached(`/v1/admin/plan/templates/${id}`), subjectMap(app)]),
    ([template, subjects]) => {
      panel.setTitle(template.name);
      const nodes = [];
      const tools = h('div', 'ak-inline');
      tools.append(
        actionButton(app, 'admin_applyStudyPlanTemplate', 'Uygula', { variant: 'primary', onClick: async () => {
          const result = await applyTemplate(app, template, ctx);
          if (result) { panel.close(); onChange?.(); }
        } }),
        actionButton(app, 'admin_updateStudyPlanTemplate', 'Düzenle', { onClick: async () => {
          panel.close();
          onEdit?.(template);
        } }),
        actionButton(app, 'admin_deleteStudyPlanTemplate', 'Sil', { variant: 'ghost', onClick: async () => {
          if (await onRemove?.(template)) panel.close();
        } }),
      );
      nodes.push(tools);
      nodes.push(card('', kv([
        ['Açıklama', template.description || '—'],
        ['Kime uygun', levelText(template)],
        ['Süre', `${num(template.durationDays)} gün`],
        ['Görev', `${num((template.tasks || []).length)} görev · toplam ${num(totalMinutes(template.tasks))} dk`],
        ['Sürüm', `${num(template.version)} · ${fmtDateTime(template.updatedAt || template.createdAt)}`],
        ['Uygulama', template.appliedCount ? `${num(template.appliedCount)} kez uygulandı` : 'Henüz uygulanmadı'],
      ])));
      const table = dataTable({
        columns: [
          { key: 'day', label: 'Gün', width: '72px', cell: (row) => whenText(row) },
          { key: 'time', label: 'Saat', width: '96px', cell: (row) => timeText(row) },
          { key: 'type', label: 'Tür', width: '120px', cell: (row) => TASK_TYPES[row.type] || row.type },
          { key: 'title', label: 'Görev', width: 'minmax(0, 2fr)', cell: (row) => {
            const cell = h('div', 'ak-cell-stack');
            cell.append(h('span', '', taskTitle(row)));
            const detail = detailText(row, subjects);
            if (detail) cell.append(h('small', '', detail));
            return cell;
          } },
          { key: 'minutes', label: 'Süre', width: '64px', align: 'num', cell: (row) => `${num(row.estimatedMinutes)} dk` },
        ],
        rows: sortTasks(template.tasks).map((task, index) => ({ ...task, id: String(index) })),
        dense: true,
        empty: empty('Görev yok'),
      });
      nodes.push(card('Görevler', table.node));
      return nodes;
    });
  panel.body.append(view.node);
  view.reload();
}

// -------------------------------------------------------- oluştur / düzenle

/** Şablon penceresi. `current` tam şablon (ayrıntı) ya da null. Sonuç kaydedilen şablon. */
async function editTemplate(app, current) {
  let tasks = null;
  const value = current
    ? { name: current.name, description: current.description || '', examTarget: current.examTarget || '',
      durationDays: current.durationDays }
    : { name: '', description: '', examTarget: '', durationDays: 7 };
  try {
    return await formDialog(app, {
      title: current ? `“${current.name}” şablonunu düzenle` : 'Yeni çalışma planı şablonu',
      description: current
        ? 'Kaydedince şablonun sürümü artar; bu şablonla önceden açılmış atamalar değişmez.'
        : 'Görevler “kaçıncı gün” ile tanımlanır; uygularken seçilen başlangıç gününe eklenir.',
      wide: true,
      value,
      fields: [
        { key: 'name', label: 'Şablon adı', required: true, maxLength: 80, wide: true,
          placeholder: 'LGS son 30 gün',
          validate: (raw) => (String(raw || '').trim().length < 2 ? 'En az 2 karakter yazın.' : null) },
        { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500, wide: true },
        { key: 'examTarget', label: 'Hedef sınav', type: 'select',
          options: optionsOf(EXAM_TARGETS, { empty: 'Belirtilmedi' }) },
        { key: 'durationDays', label: 'Süre (gün)', type: 'number', required: true, min: 1, max: 60,
          hint: 'En çok 60 gün. Görevler bu süreye yayılır.' },
        { key: 'gradeLevels', label: 'Seviye (isteğe bağlı)', type: 'custom', render: (draft, set) => {
          const chips = multiChips(GRADE_OPTIONS, (current?.gradeLevels || []).map(String), (list) => set(list));
          set(chips.value);
          return chips.node;
        } },
        { key: 'tracks', label: 'Alan (isteğe bağlı)', type: 'custom', render: (draft, set) => {
          const chips = multiChips(optionsOf(TRACKS), current?.tracks || [], (list) => set(list));
          set(chips.value);
          return chips.node;
        } },
        { key: 'tasks', label: 'Görevler', type: 'custom', required: true, render: (draft, set) => {
          tasks = taskListField(app, { mode: 'offset', tasks: current?.tasks || [],
            durationDays: value.durationDays, onChange: set });
          set(tasks.value());
          return tasks;
        }, validate: () => {
          if (tasks?.isEditing()) return 'Açık görev formunu listeye ekleyin ya da vazgeçin.';
          if (tasks?.outside()) return 'Süre dışında kalan görevler var: süreyi uzatın ya da görevlerin gününü değiştirin.';
          return null;
        } },
      ],
      submitLabel: current ? 'Şablonu kaydet' : 'Şablonu oluştur',
      onSubmit: (draft, { key }) => {
        const list = draft.tasks || [];
        if (!list.length) throw new AkademiError({ code: 'validation_error', message: 'En az bir görev ekleyin.' });
        const body = {
          name: String(draft.name || '').trim(),
          description: String(draft.description || '').trim() || null,
          gradeLevels: (draft.gradeLevels || []).map(gradeValue),
          tracks: draft.tracks || [],
          examTarget: draft.examTarget || null,
          durationDays: Number(draft.durationDays),
          tasks: list.map((task) => taskBody(task, 'offset')),
        };
        return current ? app.api.put(`/v1/admin/plan/templates/${current.id}`, body)
          : app.api.post('/v1/admin/plan/templates', body, { key });
      },
    });
  } finally {
    tasks?.destroy();
  }
}

// --------------------------------------------------------------- uygula

/**
 * Şablonu uygula. `template` liste satırı / ayrıntı ya da null (pencerede
 * seçilir). Başarıda yeni atamayı döndürür ve `ctx.onApplied(atama)` çağırır.
 */
export async function applyTemplate(app, template, ctx = {}) {
  const [classes, groups] = await loadTargets(app);
  let templates = [];
  if (!template) {
    try {
      templates = (await app.api.getCached('/v1/admin/plan/templates', { limit: 100 }))?.items || [];
    } catch (failure) {
      app.toast(problem(failure, 'Şablonlar alınamadı.'), 'bad');
      return null;
    }
    if (!templates.length) {
      app.toast('Henüz çalışma planı şablonu yok. Önce “Şablonlar” sekmesinden bir şablon oluşturun.', 'warn');
      return null;
    }
  }
  const pick = (draft) => template || templates.find((item) => item.id === draft.templateId) || null;
  let targets = null;
  const result = await formDialog(app, {
    title: template ? `“${template.name}” şablonunu uygula` : 'Şablondan plan ata',
    description: 'Görevler başlangıç gününe göre yerleşir ve hedefteki her öğrenciye koç görevi olarak düşer. '
      + 'Sonrası Atamalar sekmesinden düzenlenir ya da geri alınır.',
    wide: true,
    value: { templateId: template?.id || templates[0]?.id || '', startDate: todayIst(1),
      holidayPolicy: 'shift_next', notify: true },
    fields: [
      template ? null : { key: 'templateId', label: 'Şablon', type: 'select', required: true, wide: true,
        options: templates.map((item) => ({ value: item.id,
          label: `${item.name} · ${num(item.durationDays)} gün · ${num(item.taskCount)} görev` })) },
      { key: 'startDate', label: 'Başlangıç günü', type: 'date', required: true },
      { key: 'holidayPolicy', label: 'Tatile denk gelen görev', type: 'select', options: optionsOf(HOLIDAY_POLICIES),
        hint: 'Kurum takviminde tatil olan günler için.' },
      { key: 'notify', label: 'Öğrencilere bildirim gönder', type: 'checkbox' },
      { key: 'target', label: 'Kime', type: 'custom', required: true, render: (draft, set) => {
        targets = targetsField(app, { classes, groups, onChange: set });
        set(targets.value());
        return targets.node;
      }, validate: () => (targets?.count() ? null : 'En az bir sınıf, grup ya da öğrenci seçin.') },
      { key: 'span', label: 'Yerleşim', type: 'custom', render: (draft) => {
        const line = h('div', 'ak-muted');
        const paint = (state) => {
          const chosen = pick(state);
          line.textContent = chosen && state.startDate
            ? `${fmtDate(state.startDate)} – ${fmtDate(addDays(state.startDate, Number(chosen.durationDays || 1) - 1))} · `
              + `öğrenci başına ${num(chosen.taskCount ?? (chosen.tasks || []).length)} görev`
            : 'Başlangıç günü seçin.';
        };
        paint(draft);
        return { node: line, update: paint };
      } },
    ],
    submitLabel: 'Şablonu uygula',
    onSubmit: (draft, { key }) => {
      const chosen = pick(draft);
      if (!chosen) throw new AkademiError({ code: 'validation_error', message: 'Şablon seçin.' });
      if (draft.startDate < todayIst()) {
        throw new AkademiError({ code: 'date_in_past', message: 'Başlangıç günü geçmişte olamaz.' });
      }
      return app.api.post(`/v1/admin/plan/templates/${chosen.id}/apply`, {
        target: draft.target,
        startDate: draft.startDate,
        holidayPolicy: draft.holidayPolicy || 'shift_next',
        notify: Boolean(draft.notify),
      }, { key });
    },
  });
  targets?.destroy();
  if (!result) return null;
  const perStudent = (result.tasks || []).length;
  app.toast(`Şablon uygulandı: ${num(result.studentCount)} öğrenci × ${num(perStudent)} görev`
    + ` = ${num(result.progress?.taskInstances ?? result.studentCount * perStudent)} görev açıldı.`, 'good');
  app.api.invalidate('/v1/admin/plan/templates');
  ctx.onApplied?.(result);
  return result;
}

/** Atama penceresinde şablon bilgisi (yalnız uyarı metni). */
export function templateNotice(templateId) {
  return templateId
    ? alertBox('Bu atama bir şablondan açıldı; şablonu değiştirmek bu atamayı değiştirmez.', 'info') : null;
}
