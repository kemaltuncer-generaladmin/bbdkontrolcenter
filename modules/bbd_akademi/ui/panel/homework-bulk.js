// BBD Akademi — toplu ödev (F-A25): üç adım, önizleme, şablondan.
//
//   1. Ne?        bir ya da birkaç ödev: şablondan seç ya da yeni yaz (en çok 30)
//   2. Kime?      sınıflar + çalışma grupları + öğrenciler (birleşim), hariç tutulanlar
//   3. Ne zaman?  tek tarih · birkaç tarih · her hafta · şablonun süresi; önizleme
//
// Önizleme (`admin_previewHomeworkBulk`, yazma yok, ≤ 300 ms) adım 3'te
// kendiliğinden ve her değişiklikte (450 ms sonra) yenilenir: öğrenci, ödev ve
// teslim satırı sayısı, gruplar, tarihler, çakışmalar (aynı gün aynı ödev /
// yoğun gün). "Ödevi ver" tek istektir (`admin_createHomeworkBulk`, sabit
// Idempotency-Key — çift tıklama ikinci seri açmaz); 3.000 teslim satırını
// aşan iş arka planda koşar ve ilerlemesi pencerede görünür.
//
// Mevcut ödevlerde toplu erteleme / tarih / iptal / silme `admin_bulkUpdateHomework`
// ile Ödevler listesindeki seçimden yapılır (homework.js).

import { button, debounce, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, kpiRow } from '../../ui-kit/layout.js';
import {
  WEEKDAYS, addDays, clockInput, dateInput, errorBox, fmtDate, fmtDateTime, formDialog, istanbulStamp,
  multiChips, num, problem, searchBox, todayIst,
} from './common.js';
import { bulkTargetField } from './shared/bulk-target.js';
import { homeworkContent, removableChip, stack, topicsText } from './homework-content.js';
import { loadTargets } from './resources-common.js';
import { followBulkJob, jobProgressText, wizard } from './shared/wizard.js';

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const MAX_ITEMS = 30;
const MAX_HOMEWORK = 60;
const WHEN = {
  single: 'Tek tarih',
  dates: 'Birkaç tarih (aynı ödev her tarihte)',
  weekly: 'Her hafta',
  template: 'Şablondaki süre',
};
const CONFLICT = {
  duplicate: (row) => `“${row.title || 'Aynı ödev'}” ${fmtDate(row.date)} teslimli olarak ${num(row.studentCount)} `
    + 'öğrencide zaten var.',
  busy_day: (row) => `${fmtDate(row.date)}: ${num(row.studentCount)} öğrencinin o gün teslimli 3 ya da daha fazla `
    + 'bekleyen ödevi var (yeniyle 4+ olur).',
};

/** Şablonun okunur özeti: "Paragraf — Test 4-6 · Türkçe · 7 gün sonra 23:59". */
export function templateLine(template) {
  if (!template) return '';
  const due = template.dueAfterDays === 0 ? `aynı gün ${template.dueTime || ''}`
    : `${num(template.dueAfterDays ?? 7)} gün sonra ${template.dueTime || ''}`;
  return [template.title, template.subject?.name, due.trim()].filter(Boolean).join(' · ');
}

/** Seçili haftalık günlerin aralıktaki tarihleri ('YYYY-MM-DD'). */
function weeklyDates(start, end, weekdays) {
  if (!start || !end || end < start || !weekdays.length) return [];
  const keys = new Set(weekdays);
  const order = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  const out = [];
  for (let day = start; day <= end && out.length < 200; day = addDays(day, 1)) {
    const [year, month, date] = day.split('-').map(Number);
    if (keys.has(order[new Date(Date.UTC(year, month - 1, date)).getUTCDay()])) out.push(day);
  }
  return out;
}

/**
 * Ödev içeriği penceresi (yeni ödev satırı ya da düzenleme). `current`
 * içerik biçimindedir (title, description, subject, topics, resourceId …).
 * `save({draft, content}, {key})` verilirse sonucu döner (şablon formu).
 * @returns {Promise<{draft, content, source}|object|null>}
 */
export async function contentDialog(app, current, { title = 'Ödev içeriği', submitLabel = 'Ekle',
  extraFields = [], extraValue = {}, save = null, target = null } = {}) {
  const subjects = await app.lookups.subjects().catch(() => []);
  const pieces = homeworkContent(app, current, subjects, { target });
  try {
    const saved = await formDialog(app, {
      title,
      wide: true,
      value: { title: current?.title || '', subjectId: current?.subject?.id || '',
        description: current?.description || '', ...extraValue },
      fields: [
        ...extraFields.filter((field) => field.first),
        { key: 'title', label: 'Ödev başlığı', required: true, maxLength: 120, wide: true,
          placeholder: 'Ör. Paragraf — Test 4-6' },
        { key: 'subjectId', label: 'Ders', type: 'select', options: pieces.subjectOptions },
        { key: 'topicIds', label: 'Müfredat konuları (isteğe bağlı)', type: 'custom', render: pieces.topicField },
        { key: 'resource', label: 'Kaynak (isteğe bağlı)', type: 'custom', render: pieces.resourceField,
          validate: pieces.resourceValidate },
        { key: 'description', label: 'Açıklama', type: 'custom', render: pieces.descriptionField },
        ...extraFields.filter((field) => !field.first),
      ],
      submitLabel,
      onSubmit: async (draft, { key }) => {
        const content = pieces.content(draft);
        const subject = pieces.subjectOptions.find((option) => option.value === draft.subjectId);
        // `save` verilirse kayıt pencere içinde yapılır: hata (ör. ad çakışması) formda görünür.
        if (save) return save({ draft, content }, { key });
        return {
          draft,
          content,
          source: {
            ...content,
            subject: draft.subjectId ? { id: draft.subjectId, name: subject?.label || draft.subjectId } : null,
            topics: [...pieces.topics].map(([id, name]) => ({ id, name })),
          },
        };
      },
    });
    return saved && saved !== true ? saved : null;
  } finally {
    pieces.dispose();
  }
}

/**
 * Toplu ödev sihirbazı.
 * @param {object} app
 * @param {object} options  template: başlangıçta seçili ödev şablonu (Şablonlar ekranından)
 * @returns {Promise<object|null>} biten toplu iş (`BulkJob`) ya da null
 */
export async function bulkHomeworkWizard(app, { template = null } = {}) {
  loadStyles(import.meta.url, 'homework.css');
  const [classes, groups] = await loadTargets(app);
  let seqNo = 0;
  const items = [];
  const addTemplate = (row) => {
    if (items.some((item) => item.templateId === row.id)) {
      app.toast('Bu şablon zaten listede.', 'warn');
      return false;
    }
    seqNo += 1;
    items.push({ key: seqNo, templateId: row.id, template: row });
    return true;
  };
  if (template) addTemplate(template);

  const when = {
    mode: template ? 'template' : 'single',
    day: addDays(todayIst(), 7),
    clock: '23:59',
    dates: [],
    weekdays: [],
    start: todayIst(),
    end: addDays(todayIst(), 27),
    notify: true,
  };
  const allTemplates = () => items.length > 0 && items.every((item) => item.templateId && !item.content);

  let target = null;
  // "Kime?" önce seçilip "Ne?"ye dönülürse program seçici o hedefin seviyesini önerir.
  const contentTarget = () => {
    if (!target || target.problem()) return null;
    const { classGroupIds, studentGroupIds, studentIds } = target.value({ bundle: false });
    return { classGroupIds, studentGroupIds, studentIds };
  };

  // ======================================================= 1. Ne?
  const stepWhat = (ctl) => {
    const node = h('div', 'ak-stack');
    const list = h('div', 'ak-hwb-items');
    list.setAttribute('aria-live', 'polite');
    const pickerSlot = h('div', 'ak-stack');
    pickerSlot.hidden = true;

    const paint = () => {
      list.replaceChildren();
      if (!items.length) {
        list.append(h('div', 'ak-muted', 'Henüz ödev eklenmedi. Şablondan seçin ya da yeni ödev yazın.'));
      }
      items.forEach((item, index) => {
        const row = h('div', 'ak-hwb-item');
        const main = item.templateId && !item.content
          ? stack(`${index + 1}. ${item.template?.name || 'Şablon'}`, `Şablon · ${templateLine(item.template)}`)
          : stack(`${index + 1}. ${item.source?.title || 'Ödev'}`,
            [item.source?.subject?.name, topicsText(item.source?.topics), item.source?.resourceId ? 'kaynaklı' : '']
              .filter(Boolean).join(' · ') || 'Yeni ödev');
        const tools = h('div', 'ak-row-actions');
        if (item.content) {
          tools.append(button('Düzenle', { variant: 'ghost', onClick: async () => {
            const next = await contentDialog(app, item.source, { title: 'Ödevi düzenle', submitLabel: 'Kaydet',
              target: contentTarget });
            if (next) { item.content = next.content; item.source = next.source; paint(); }
          } }));
        }
        const remove = button('Çıkar', { variant: 'ghost', onClick: () => {
          items.splice(index, 1);
          paint();
        } });
        remove.setAttribute('aria-label', `${index + 1}. ödevi listeden çıkar`);
        tools.append(remove);
        row.append(main, tools);
        list.append(row);
      });
      ctl.note(items.length ? `${num(items.length)} ödev satırı` : '');
    };

    // Şablon seçici: ilk açılışta son güncellenen şablonlar, yazınca arama.
    let token = 0;
    const results = h('div', 'ak-hwb-templates');
    const showTemplates = async (text = '') => {
      const mine = ++token;
      results.replaceChildren(h('div', 'ak-muted', 'Şablonlar yükleniyor…'));
      try {
        const data = await app.api.get('/v1/admin/homework-templates',
          { q: text.length >= 2 ? text : undefined, limit: 12 });
        if (mine !== token) return;
        results.replaceChildren();
        const rows = data?.items || [];
        if (!rows.length) {
          results.append(h('div', 'ak-muted', text ? 'Eşleşen şablon yok.'
            : 'Henüz ödev şablonu yok. Şablonlar ekranından ya da “Yeni ödev yaz” ile başlayın.'));
        }
        for (const row of rows) {
          const option = h('button', 'ak-picker-row');
          option.type = 'button';
          option.append(h('b', '', row.name), h('span', '', templateLine(row)));
          option.addEventListener('click', () => {
            if (items.length >= MAX_ITEMS) { app.toast(`En çok ${MAX_ITEMS} ödev satırı eklenebilir.`, 'warn'); return; }
            if (addTemplate(row)) { paint(); ctl.clearError(); }
          });
          results.append(option);
        }
      } catch (failure) {
        if (mine !== token) return;
        results.replaceChildren(errorBox(failure, () => showTemplates(text)));
      }
    };
    const search = searchBox({ placeholder: 'Şablon ara (ad ya da başlık)', label: 'Şablon ara',
      onSearch: (text) => showTemplates(text) });
    pickerSlot.append(search.node, results);

    const fromTemplate = button('Şablondan ekle', { onClick: () => {
      pickerSlot.hidden = !pickerSlot.hidden;
      fromTemplate.setAttribute('aria-expanded', pickerSlot.hidden ? 'false' : 'true');
      if (!pickerSlot.hidden) { showTemplates(search.value); search.node.focus(); }
    } });
    fromTemplate.setAttribute('aria-expanded', 'false');
    const write = button('Yeni ödev yaz', { variant: 'primary', onClick: async () => {
      if (items.length >= MAX_ITEMS) { app.toast(`En çok ${MAX_ITEMS} ödev satırı eklenebilir.`, 'warn'); return; }
      const next = await contentDialog(app, null, { title: 'Yeni ödev', target: contentTarget });
      if (!next) return;
      seqNo += 1;
      items.push({ key: seqNo, templateId: null, content: next.content, source: next.source });
      paint();
      ctl.clearError();
    } });
    const tools = h('div', 'ak-inline');
    tools.append(write, fromTemplate, h('span', 'ak-muted', 'Aynı öğrencilere birden çok ödev tek seferde verilebilir.'));
    node.append(tools, pickerSlot, list);
    paint();
    return { node, enter: paint, destroy: () => search.destroy() };
  };

  // ======================================================= 2. Kime?
  const stepWho = (ctl) => {
    target = bulkTargetField(app, { classes, groups, modes: ['pick'], onChange: () => {
      ctl.clearError();
      ctl.note(`Hedef: ${target.summary()}`);
    } });
    const node = h('div', 'ak-stack');
    node.append(h('div', 'ak-muted', 'Seçilenler birleştirilir; aynı öğrenciye ödev bir kez düşer. Grup üyeliği '
      + 'bugünkü üyeliktir.'), target.node);
    return { node, enter: () => ctl.note(`Hedef: ${target.summary()}`), destroy: () => target.destroy() };
  };

  // ======================================================= 3. Ne zaman?
  const stepWhen = (ctl) => {
    const node = h('div', 'ak-stack');
    const disposers = [];
    const modes = h('div', 'ak-bt-modes');
    modes.setAttribute('role', 'radiogroup');
    modes.setAttribute('aria-label', 'Teslim tarihi türü');
    const panes = {};
    const radios = {};
    for (const key of Object.keys(WHEN)) {
      const label = h('label', 'ak-bt-mode');
      const input = h('input');
      input.type = 'radio';
      input.name = 'ak-hwb-when';
      input.value = key;
      input.addEventListener('change', () => { if (input.checked) { when.mode = key; paint(); changed(); } });
      label.append(input, h('span', '', WHEN[key]));
      radios[key] = { label, input };
      modes.append(label);
    }

    // tek tarih
    const single = h('div', 'ak-inline ak-hw-due');
    const day = dateInput(when.day, (iso) => { when.day = iso || ''; changed(); }, 'Son teslim günü');
    disposers.push(() => day.destroy());
    const clock = clockInput(when.clock, (value) => { when.clock = value; changed(); }, 'Son teslim saati');
    single.append(day.node, clock.node, h('span', 'ak-muted', 'İstanbul saati'));
    panes.single = single;

    // birkaç tarih
    const multi = h('div', 'ak-stack');
    const chosen = h('div', 'ak-chips');
    let pending = addDays(todayIst(), 7);
    const multiDay = dateInput(pending, (iso) => { pending = iso || ''; }, 'Eklenecek tarih');
    disposers.push(() => multiDay.destroy());
    const multiClock = clockInput(when.clock, (value) => { when.clock = value; changed(); }, 'Teslim saati');
    const paintDates = () => {
      chosen.replaceChildren();
      for (const iso of [...when.dates].sort()) {
        chosen.append(removableChip(fmtDate(iso), () => {
          when.dates = when.dates.filter((item) => item !== iso);
          paintDates();
          changed();
        }, 'Tarihi çıkar'));
      }
      if (!when.dates.length) chosen.append(h('span', 'ak-muted', 'Henüz tarih eklenmedi.'));
    };
    const addDate = button('Tarihi ekle', { onClick: () => {
      if (!pending) return;
      if (!when.dates.includes(pending)) when.dates.push(pending);
      paintDates();
      changed();
    } });
    const multiRow = h('div', 'ak-inline ak-hw-due');
    multiRow.append(multiDay.node, addDate, h('span', 'ak-muted', 'saat'), multiClock.node);
    multi.append(multiRow, chosen);
    paintDates();
    panes.dates = multi;

    // her hafta
    const weekly = h('div', 'ak-stack');
    const days = multiChips(WEEKDAYS.map(([key, , long]) => ({ value: key, label: long })), when.weekdays,
      (list) => { when.weekdays = list; changed(); });
    days.node.setAttribute('role', 'group');
    days.node.setAttribute('aria-label', 'Haftanın günleri');
    const startField = dateInput(when.start, (iso) => { when.start = iso || ''; changed(); }, 'Başlangıç günü');
    const endField = dateInput(when.end, (iso) => { when.end = iso || ''; changed(); }, 'Bitiş günü');
    disposers.push(() => startField.destroy(), () => endField.destroy());
    const weeklyClock = clockInput(when.clock, (value) => { when.clock = value; changed(); }, 'Teslim saati');
    const range = h('div', 'ak-inline ak-hw-due');
    range.append(h('span', 'ak-muted', 'Başlangıç'), startField.node, h('span', 'ak-muted', 'Bitiş'), endField.node,
      h('span', 'ak-muted', 'saat'), weeklyClock.node);
    weekly.append(days.node, range, h('div', 'ak-muted', 'Seçili günlerde, bitişe kadar (en çok 16 hafta) her '
      + 'tarih için ayrı ödev açılır.'));
    panes.weekly = weekly;

    panes.template = h('div', 'ak-muted', 'Her ödev şablonundaki varsayılan süreyle verilir (bugün + gün sayısı, '
      + 'şablondaki saat).');

    const notify = h('label', 'ak-inline');
    const notifyCheck = h('input', 'kit-check');
    notifyCheck.type = 'checkbox';
    notifyCheck.checked = when.notify;
    notifyCheck.addEventListener('change', () => { when.notify = notifyCheck.checked; });
    notify.append(notifyCheck, h('span', '', 'Öğrencilere (ve velilerine) tek özet bildirim gönder'));

    const preview = h('div', 'ak-hwb-preview');
    const previewTitle = h('div', 'ak-hwb-preview-title', 'Önizleme');
    const previewBody = h('div', 'ak-stack');
    previewBody.setAttribute('aria-live', 'polite');
    preview.append(previewTitle, previewBody);

    node.append(modes, ...Object.values(panes), notify, preview);

    function paint() {
      const templatesOnly = allTemplates();
      radios.template.label.hidden = !templatesOnly;
      if (when.mode === 'template' && !templatesOnly) when.mode = 'single';
      for (const [key, radio] of Object.entries(radios)) radio.input.checked = key === when.mode;
      for (const [key, pane] of Object.entries(panes)) pane.hidden = key !== when.mode;
    }

    let token = 0;
    const run = async () => {
      const mine = ++token;
      const reason = whenProblem() || target?.problem();
      if (reason) {
        previewBody.replaceChildren(h('div', 'ak-muted', reason));
        return;
      }
      if (!app.can('admin_previewHomeworkBulk')) {
        previewBody.replaceChildren(h('div', 'ak-muted', 'Önizleme bu hesaba açık değil; “Ödevi ver” yine çalışır.'));
        return;
      }
      previewBody.replaceChildren(h('div', 'ak-muted', 'Hesaplanıyor…'));
      try {
        const result = await app.api.post('/v1/admin/homework/bulk/preview', body());
        if (mine !== token) return;
        previewBody.replaceChildren(...previewNodes(result));
        ctl.note(`${num(result.studentCount)} öğrenciye ${num(result.homeworkCount)} ödev`);
      } catch (failure) {
        if (mine !== token) return;
        previewBody.replaceChildren(alertBox(problem(failure), 'bad'));
        ctl.note('');
      }
    };
    const changed = debounce(run, 450);
    disposers.push(() => changed.cancel?.());
    paint();
    return {
      node,
      enter: () => { paint(); run(); },
      destroy: () => { for (const fn of disposers) fn(); },
    };
  };

  function dueDates() {
    if (when.mode === 'single') return when.day ? [when.day] : [];
    if (when.mode === 'dates') return [...when.dates].sort();
    if (when.mode === 'weekly') return weeklyDates(when.start, when.end, when.weekdays);
    return [];
  }

  function whenProblem() {
    if (!items.length) return 'Önce 1. adımda en az bir ödev ekleyin.';
    if (when.mode === 'template') return null;
    if (!CLOCK.test(when.clock || '')) return 'Saati 18:30 gibi yazın.';
    const dates = dueDates();
    if (when.mode === 'single' && !when.day) return 'Son teslim günü seçin.';
    if (when.mode === 'dates' && !dates.length) return 'En az bir tarih ekleyin.';
    if (when.mode === 'weekly') {
      if (!when.weekdays.length) return 'En az bir gün seçin.';
      if (!when.start || !when.end || when.end < when.start) return 'Bitiş günü başlangıçtan önce olamaz.';
      if (when.end > addDays(when.start, 16 * 7 - 1)) return 'Haftalık tekrar en çok 16 hafta sürebilir.';
      if (!dates.length) return 'Seçilen aralıkta bu günlerden hiç yok.';
    }
    const now = Date.now();
    if (dates.some((iso) => new Date(istanbulStamp(iso, when.clock)).getTime() < now)) {
      return 'Son teslim geçmiş bir zaman olamaz.';
    }
    if (dates.length * items.length > MAX_HOMEWORK) {
      return `Toplam ödev (satır × tarih) en çok ${MAX_HOMEWORK} olabilir; şu an ${num(dates.length * items.length)}.`;
    }
    return null;
  }

  function body() {
    const out = {
      target: target.value({ bundle: false }),
      items: items.map((item) => ({
        templateId: item.templateId || null,
        content: item.content || null,
        dueAt: when.mode === 'single' ? istanbulStamp(when.day, when.clock) : null,
      })),
      notify: Boolean(when.notify),
    };
    if (when.mode === 'dates') out.schedule = { dueAts: dueDates().map((iso) => istanbulStamp(iso, when.clock)) };
    if (when.mode === 'weekly') {
      out.schedule = { recurrence: { weekdays: when.weekdays, dueTime: when.clock, startDate: when.start,
        endDate: when.end } };
    }
    return out;
  }

  function previewNodes(result) {
    const nodes = [kpiRow([
      { label: 'Öğrenci', value: num(result.studentCount) },
      { label: 'Hariç tutulan', value: num(result.excludedCount) },
      { label: 'Ödev', value: num(result.homeworkCount) },
      { label: 'Teslim satırı', value: num(result.submissionCount), title: 'öğrenci × ödev' },
    ])];
    if (!result.studentCount) nodes.push(alertBox('Seçilen hedefte etkin öğrenci yok.', 'warn'));
    if ((result.groups || []).length) {
      nodes.push(h('div', 'ak-muted', result.groups.map((group) => `${group.label}: ${num(group.studentCount)} öğrenci`)
        .join(' · ')));
    }
    const dates = result.occurrences || [];
    if (dates.length) {
      const list = h('ul', 'ak-hwb-dates');
      for (const row of dates.slice(0, 8)) list.append(h('li', '', `${row.title} — ${fmtDateTime(row.dueAt)}`));
      if (dates.length > 8) list.append(h('li', 'ak-muted', `+${num(dates.length - 8)} ödev daha`));
      nodes.push(list);
    }
    for (const row of result.conflicts || []) {
      nodes.push(alertBox((CONFLICT[row.kind] || ((item) => `${fmtDate(item.date)}: çakışma`))(row), 'warn'));
    }
    if (result.runsInBackground) {
      nodes.push(alertBox('Büyük işlem: ödevler arka planda hazırlanır; ilerleme bu pencerede görünür.', 'info'));
    }
    return nodes;
  }

  const job = await wizard(app, {
    title: 'Toplu ödev',
    finishLabel: 'Ödevi ver',
    start: template ? 1 : 0,
    steps: [
      { key: 'what', label: 'Ne?', render: stepWhat,
        validate: () => (items.length ? null : 'En az bir ödev ekleyin (şablondan ya da yeni).') },
      { key: 'who', label: 'Kime?', render: stepWho, validate: () => target?.problem() || null },
      { key: 'when', label: 'Ne zaman?', render: stepWhen, validate: () => whenProblem() },
    ],
    onFinish: async ({ key, progress }) => {
      const started = await app.api.post('/v1/admin/homework/bulk', body(), { key });
      const done = await followBulkJob(app, started, {
        onProgress: (current) => {
          if (current && ['queued', 'running'].includes(current.status)) progress(jobProgressText(current, 'teslim satırı'));
        } });
      if (done && ['queued', 'running'].includes(done.status)) {
        app.toast('Ödevler arka planda hazırlanmaya devam ediyor; birkaç dakika sonra listeyi yenileyin.', 'warn');
      }
      return done || started;
    },
  });
  if (!job) return null;
  const result = job.homework;
  if (result) {
    app.toast(`${num(result.studentCount)} öğrenciye ${num(result.homeworkCount)} ödev verildi`
      + `${result.notifiedStudentCount ? ` · ${num(result.notifiedStudentCount)} bildirim` : ''}.`, 'good');
  }
  return job;
}
