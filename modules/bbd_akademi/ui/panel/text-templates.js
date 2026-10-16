// BBD Akademi — koçluk metin şablonu seçici ve "Şablon olarak kaydet" (Akademi README §14 madde 115).
//
// Kullanıcı (16.10.2026): "koçluk kısmına şablon ekleyelim". Görüşme notu, soru (hoca) yanıtı, check-in yorumu,
// veli görüşmesi notu ve tebrik formlarında AYNI alan durur:
//
//   · "Şablondan doldur": form içinde açılan liste (arama, türe göre süzülmüş; kurum + hazır şablonlar). Seçilen
//     şablon Akademi'de öğrencinin adı, velisi, koçu, ders ve tarihle DOLDURULUR (`admin_renderCoachTextTemplate`)
//     ve forma yerleşir — alan boşsa metin olduğu gibi, doluysa yazılanın altına eklenir; görüşme notunda eylem
//     maddeleri ve varsayılan görünürlük de gelir. Metin düzenlenebilir; asıl kayıt formun kendi işlemidir.
//   · "Şablon olarak kaydet": formdaki metni (ve eylem maddelerini) KM'de KURUM şablonu yapar
//     (`admin_createCoachTextTemplate`, scope=institution) — bütün koçların seçicisinde görünür.
//
// İç içe pencere açılmaz (odak tuzağı / Esc çakışması): seçici ve kaydetme formu alanın içinde açılır.
// Saf mantık `text-templates-logic.js`'tedir.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge } from '../../ui-kit/layout.js';
import { problem } from './api.js';
import {
  KIND_LABELS, SCOPE_LABELS, SCOPE_TONES, TEXT_TEMPLATES, TEXTS, actionsText, filterTemplates, mergeActions,
  mergeText, parseActions, preview, saveScope, templateBody, unfilledNote, withInlineActions,
} from './text-templates-logic.js';

/** Türün şablon listesi (60 sn önbellek; yazınca `invalidateTemplates`). */
export function loadTemplates(app, kind = '') {
  return app.api.getCached(TEXT_TEMPLATES, kind ? { kind } : undefined);
}

export function invalidateTemplates(app) {
  app.api.invalidate(TEXT_TEMPLATES);
}

/**
 * `formDialog` özel alanı. `null` döner (form süzer) — kullanıcı şablon listesini göremiyorsa.
 *
 * @param {object} spec
 * @param {string} spec.kind      — `meeting_note` | `question_answer` | `checkin_comment` | `parent_meeting` | `kudos` | `message`
 * @param {string} spec.target    — metnin form alanı (ör. `text`, `comment`, `note`)
 * @param {string} [spec.actions] — eylem maddeleri alanı (çok satırlı metin)
 * @param {string} [spec.visibility] — görünürlük alanı (görüşme notu)
 * @param {(draft)=>object} [spec.context] — doldurma bağlamı: {studentId, guardianId, coachId, subjectId, date}
 * @param {number} [spec.maxLength] — hedef alanın sınırı
 */
export function templateField(app, { kind, target, actions = null, visibility = null, context = () => ({}),
  maxLength = null, label = 'Şablon', perStudent = false, inlineActions = false }) {
  if (!app.can('admin_listCoachTextTemplates')) return null;
  return {
    key: `_template_${target}`,
    label,
    type: 'custom',
    wide: true,
    render: (draft, _set, form) => templateTools(app, { kind, target, actions, visibility, context, maxLength, perStudent,
      inlineActions,
      form: form || { setField: (fieldKey, raw) => { draft[fieldKey] = raw; }, get: () => draft } }),
  };
}

/** Düz `<textarea>` için form uyarlayıcısı (çekmecedeki check-in yorumu, hoca yanıtı). */
export function textareaForm(input, key = 'text') {
  return {
    get: () => ({ [key]: input.value }),
    setField: (fieldKey, raw) => {
      if (fieldKey !== key) return;
      input.value = raw;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    },
  };
}

/** Kenar çubuğu olmadan seçici + kaydet: `null` = kullanıcı şablon listesini göremiyor. */
export function templateTools(app, spec) {
  if (!app.can('admin_listCoachTextTemplates')) return null;
  loadStyles(import.meta.url, 'text-templates.css');
  return templateControl(app, { target: 'text', actions: null, visibility: null, context: () => ({}), maxLength: null,
    perStudent: false, inlineActions: false, ...spec });
}

/** Toplu gönderimde öğrenciye özgü yer tutucular gönderirken sunucuda her öğrenci için doldurulur. */
const PER_STUDENT_KEYS = ['ogrenci_adi', 'ogrenci_tam_adi', 'sinif', 'veli_adi'];

function templateControl(app, { kind, target, actions, visibility, context, maxLength, form, perStudent, inlineActions }) {
  const node = h('div', 'ak-tt');
  const bar = h('div', 'ak-tt-bar');
  const pickButton = button(TEXTS.pick, { variant: 'ghost', onClick: () => toggle('pick') });
  pickButton.setAttribute('aria-expanded', 'false');
  const canSave = app.can('admin_createCoachTextTemplate');
  const saveButton = button(TEXTS.save, { variant: 'ghost', onClick: () => toggle('save') });
  saveButton.setAttribute('aria-expanded', 'false');
  saveButton.hidden = !canSave;
  const status = h('small', 'ak-muted ak-tt-status');
  status.setAttribute('role', 'status');
  bar.append(pickButton, saveButton, status);
  const panel = h('div', 'ak-tt-panel');
  panel.hidden = true;
  node.append(bar, panel);
  let open = '';
  let list = null;

  function toggle(which) {
    open = open === which ? '' : which;
    pickButton.setAttribute('aria-expanded', String(open === 'pick'));
    saveButton.setAttribute('aria-expanded', String(open === 'save'));
    panel.hidden = !open;
    status.textContent = '';
    if (open === 'pick') paintPicker();
    if (open === 'save') paintSave();
  }

  async function ensureList() {
    if (!list) list = await loadTemplates(app, kind);
    return list;
  }

  async function paintPicker() {
    panel.replaceChildren(h('p', 'ak-muted', 'Şablonlar yükleniyor…'));
    let data;
    try {
      data = await ensureList();
    } catch (failure) {
      panel.replaceChildren(h('p', 'kit-field-error', problem(failure, 'Şablonlar okunamadı.')));
      return;
    }
    const search = h('input', 'kit-input ak-tt-search');
    search.type = 'search';
    search.placeholder = TEXTS.search;
    search.setAttribute('aria-label', `${KIND_LABELS[kind] || ''} şablonu ara`);
    const items = h('div', 'ak-tt-list');
    items.setAttribute('role', 'list');
    const paintItems = () => {
      const found = filterTemplates(data?.items || [], { kind, q: search.value });
      if (!found.length) {
        items.replaceChildren(h('p', 'ak-muted', search.value ? 'Aramaya uyan şablon yok.' : `${TEXTS.empty}. ${TEXTS.emptyHint}`));
        return;
      }
      items.replaceChildren(...found.slice(0, 50).map((item) => {
        const row = h('button', 'ak-tt-item');
        row.type = 'button';
        row.setAttribute('role', 'listitem');
        const head = h('span', 'ak-tt-item-head');
        head.append(h('strong', '', item.title), badge(SCOPE_LABELS[item.scope] || item.scope, SCOPE_TONES[item.scope]));
        row.append(head, h('small', '', preview(item.body)));
        row.addEventListener('click', () => pick(item, row));
        return row;
      }));
    };
    search.addEventListener('input', paintItems);
    panel.replaceChildren(search, items, h('small', 'kit-field-hint', TEXTS.hint));
    paintItems();
    search.focus();
  }

  async function pick(item, row) {
    if (!app.can('admin_renderCoachTextTemplate')) {
      status.textContent = 'Şablonu doldurma yetkiniz yok.';
      return;
    }
    row.disabled = true;
    try {
      const draft = form.get();
      const ctx = Object.fromEntries(Object.entries(context(draft) || {}).filter(([, value]) => value));
      const filled = await app.api.post(`${TEXT_TEMPLATES}/${encodeURIComponent(item.id)}/render`, ctx);
      const body = inlineActions && !actions ? withInlineActions(filled.text, filled.actionItems) : filled.text;
      const merged = mergeText(draft[target], body, maxLength || filled.maxLength);
      form.setField(target, merged.text);
      if (actions && filled.actionItems?.length) {
        form.setField(actions, actionsText(mergeActions(parseActions(draft[actions], 20), filled.actionItems, 20)));
      }
      if (visibility && filled.noteVisibility && !String(draft[target] || '').trim()) {
        form.setField(visibility, filled.noteVisibility);
      }
      toggle('');
      const missing = (filled.unfilled || []).filter((key) => !perStudent || !PER_STUDENT_KEYS.includes(key));
      status.textContent = [TEXTS.filled, unfilledNote(missing),
        perStudent && (filled.unfilled || []).some((key) => PER_STUDENT_KEYS.includes(key))
          ? 'Öğrenci adı gönderilirken her öğrenci için ayrı doldurulur.' : '',
        merged.truncated || filled.truncated ? 'Metin alanın sınırında kesildi.' : ''].filter(Boolean).join(' ');
      list = null;   // kullanım sayısı değişti
      invalidateTemplates(app);
    } catch (failure) {
      status.textContent = problem(failure, 'Şablon doldurulamadı.');
      row.disabled = false;
    }
  }

  async function paintSave() {
    let data = null;
    try {
      data = await ensureList();
    } catch {
      data = null;
    }
    const scope = saveScope(data) || 'institution';
    const draft = form.get();
    const title = h('input', 'kit-input');
    title.maxLength = 80;
    title.placeholder = 'Şablon adı (ör. Deneme sonrası değerlendirme)';
    title.setAttribute('aria-label', 'Şablon adı');
    const note = h('small', 'kit-field-hint', scope === 'institution'
      ? 'Kurum şablonu olur: bütün koç ve öğretmenlerin seçicisinde görünür. Öğrenci adını {ogrenci_adi} gibi yer tutucuyla yazarsanız her öğrencide doldurulur.'
      : 'Kişisel şablonunuz olur; yalnız siz görürsünüz.');
    const error = h('span', 'kit-field-error');
    const save = button('Kaydet', { variant: 'primary', onClick: async () => {
      const current = form.get();
      const built = templateBody({ title: title.value, body: current[target],
        actionItems: actions ? current[actions] : undefined,
        noteVisibility: visibility ? current[visibility] : undefined }, { kind, scope, maxLengths: data?.maxLengths });
      if (built.error) { error.textContent = built.error; return; }
      save.disabled = true;
      try {
        await app.api.post(TEXT_TEMPLATES, built.body);
        invalidateTemplates(app);
        list = null;
        toggle('');
        status.textContent = `${TEXTS.saved} (${KIND_LABELS[kind]} · ${SCOPE_LABELS[scope]})`;
        app.toast(TEXTS.saved, 'good');
      } catch (failure) {
        error.textContent = problem(failure, 'Şablon kaydedilemedi.');
        save.disabled = false;
      }
    } });
    const row = h('div', 'ak-tt-save');
    row.append(title, save, button('Vazgeç', { variant: 'ghost', onClick: () => toggle('') }));
    panel.replaceChildren(row, note, error);
    if (!String(draft[target] || '').trim()) error.textContent = 'Önce metni yazın; kaydedilen metin şablon olur.';
    title.focus();
  }

  return node;
}
