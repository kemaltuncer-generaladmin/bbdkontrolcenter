// BBD Akademi — Sözleşme madde şablonları (Sözleşmeler → "Madde şablonları"; A20–A22, K4).
//
// Tür seç → etkin sürümün maddeleri (başlık + öğeler; ekle / sil / sırala) → yer tutucu yardımı (A20 `placeholders`,
// tıkla → imleçteki kutuya eklenir) → canlı önizleme (yer tutucular örnek değerle) → "Yeni sürüm olarak kaydet" (A22,
// not ile). Sürüm geçmişi altta. KURAL: şablon değişikliği yalnız YENİ sözleşmelere uygulanır; mevcut sözleşme kendi
// metniyle kalır (tek sözleşmede madde değişikliği öğrencinin Sözleşmeler sekmesinden, yeni sürümle).
// Kaydetmek kurum ayarıdır (KM izni `bbd_akademi.settings`); izni olmayan salt okur.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, card } from '../../../ui-kit/layout.js';
import { confirmAction, fmtDateTime, formDialog, loader } from '../common.js';
import { KIND_LABEL } from './documents-logic.js';
import {
  TEMPLATE_KINDS, addClause, addItem, changeSummary, cloneClauses, clausesEqual, insertToken, moveClause, moveItem,
  normalizeClauses, previewModel, removeClause, removeItem, setItem, setTitle, unknownPlaceholders, validateClauses,
} from './templates-logic.js';

const PATH = '/v1/admin/contracts/templates';

export function templatesTab(app) {
  const node = h('div', 'ak-stack ak-szb-templates');
  const canSave = app.can('admin_saveContractTemplate');
  let kind = TEMPLATE_KINDS[0];
  let placeholders = [];
  let saved = [];             // sunucudaki etkin sürümün maddeleri
  let clauses = [];           // düzenleyicideki hali
  let detail = null;
  let focused = null;         // yer tutucunun ekleneceği son kutu: {clause, item|null, input}

  const kindChips = h('div', 'ak-chips');
  kindChips.setAttribute('role', 'group');
  kindChips.setAttribute('aria-label', 'Belge türü');
  const chipOf = new Map();
  for (const key of TEMPLATE_KINDS) {
    const chip = h('button', 'kit-chip', KIND_LABEL[key]);
    chip.type = 'button';
    chip.addEventListener('click', () => switchKind(key));
    chipOf.set(key, chip);
    kindChips.append(chip);
  }
  const paintChips = () => {
    for (const [key, chip] of chipOf) {
      chip.classList.toggle('on', key === kind);
      chip.setAttribute('aria-pressed', key === kind ? 'true' : 'false');
    }
  };
  paintChips();

  node.append(alertBox('Değişiklik yalnız YENİ sözleşmelere uygulanır. Mevcut sözleşmeler kendi metniyle kalır; tek '
    + 'öğrencinin sözleşmesinde madde değiştirmek için öğrencinin Sözleşmeler sekmesinde "Maddeleri düzenle".', 'warn'),
  kindChips);

  const view = loader(async () => {
    const [list, one] = await Promise.all([
      app.api.getCached(PATH).catch(() => null),
      app.api.get(`${PATH}/${encodeURIComponent(kind)}`),
    ]);
    return { list, one };
  }, (data) => render(data), { rows: 8, columns: 2 });
  node.append(view.node);

  async function switchKind(next) {
    if (next === kind) return;
    if (!clausesEqual(saved, clauses)) {
      const ok = await confirmAction(app, { title: 'Kaydedilmemiş değişiklik var',
        description: `${KIND_LABEL[kind]} şablonundaki değişiklikler kaydedilmedi. Yine de geçilsin mi?`,
        confirmLabel: 'Değişiklikleri at', danger: true });
      if (!ok) return;
    }
    kind = next;
    paintChips();
    view.reload();
  }

  // ------------------------------------------------------------ çizim
  const editorSlot = h('div', 'ak-szb-clauses');
  const previewSlot = h('div', 'ak-szb-paper');
  const status = h('div', 'ak-set-savebar');
  const statusText = h('span', 'ak-set-savebar-text', 'Değişiklik yok');
  const reset = button('Değişiklikleri geri al', { variant: 'ghost', onClick: () => {
    clauses = cloneClauses(saved);
    paintEditor();
  } });
  const save = button('Yeni sürüm olarak kaydet', { variant: 'primary', onClick: () => submit() });
  status.append(statusText, h('span', 'kit-spacer'), reset, save);
  const warnings = h('div');

  function render({ list, one }) {
    placeholders = Array.isArray(list?.placeholders) ? list.placeholders
      : (Array.isArray(one?.placeholders) ? one.placeholders : []);
    detail = one || {};
    saved = cloneClauses(detail.active?.clauses);
    clauses = cloneClauses(saved);
    if (!clauses.length) clauses = addClause([], 0);

    const head = h('div', 'ak-muted');
    head.textContent = detail.active
      ? `Etkin sürüm ${detail.active.version} · ${fmtDateTime(detail.active.createdAt)}`
        + `${detail.active.createdByLabel ? ` · ${detail.active.createdByLabel}` : ''}`
        + `${detail.active.note ? ` · “${detail.active.note}”` : ''}`
      : 'Bu tür için henüz şablon yok — ilk sürümü yazın.';

    const left = h('div', 'ak-stack');
    const addLast = button('Madde ekle', { onClick: () => {
      clauses = addClause(clauses);
      paintEditor();
      editorSlot.querySelector('.ak-szb-clause:last-child input')?.focus();
    } });
    addLast.hidden = !canSave;
    left.append(canSave ? status : alertBox('Şablonu yalnız kurum ayarı yetkisi olanlar değiştirir; siz '
      + 'görüntülüyorsunuz.', 'info'), head, warnings, editorSlot, addLast);

    const right = h('div', 'ak-stack');
    right.append(card('Yer tutucular', placeholderChips(),
      'Tıklayınca imlecin olduğu kutuya eklenir; yeni sözleşmede gerçek değerle dolar.'),
    card('Önizleme', previewSlot, 'Yer tutucular örnek değerle gösterilir. Sözleşme PDF\'inde aynı sırayla basılır.'),
    card('Sürüm geçmişi', historyList()));
    const split = h('div', 'ak-szb-split');
    split.append(left, right);
    paintEditor();
    return split;
  }

  function placeholderChips() {
    const box = h('div', 'ak-szb-ph');
    if (!placeholders.length) {
      box.append(h('span', 'ak-muted', 'Akademi yer tutucu listesi vermedi.'));
      return box;
    }
    for (const item of placeholders) {
      const key = String(item.key || '').replace(/^\{|\}$/g, '');
      const chip = h('button', 'kit-chip', `{${key}}`);
      chip.type = 'button';
      chip.title = `${item.label || key}${item.example ? ` — ör. ${item.example}` : ''}`;
      chip.disabled = !canSave;
      // Odak kutudan çıkmasın: fare basışında varsayılan (odak alma) engellenir.
      chip.addEventListener('mousedown', (event) => event.preventDefault());
      chip.addEventListener('click', () => insertPlaceholder(key));
      box.append(chip);
    }
    return box;
  }

  function insertPlaceholder(key) {
    if (!focused?.input?.isConnected) {
      app.toast('Önce yer tutucunun ekleneceği kutuya tıklayın.', 'warn');
      return;
    }
    const { input } = focused;
    const result = insertToken(input.value, input.selectionStart, input.selectionEnd, key);
    input.value = result.text;
    if (focused.item === null) clauses = setTitle(clauses, focused.clause, result.text);
    else clauses = setItem(clauses, focused.clause, focused.item, result.text);
    input.focus();
    input.setSelectionRange(result.caret, result.caret);
    paintStatus();
  }

  function historyList() {
    const history = Array.isArray(detail?.history) ? detail.history : [];
    const box = h('ul', 'ak-szb-history');
    if (!history.length) {
      box.append(h('li', 'ak-muted', 'Önceki sürüm yok.'));
      return box;
    }
    for (const item of history) {
      const row = h('li', 'ak-cell-stack');
      row.append(h('b', '', `Sürüm ${item.version}`), h('small', '', [fmtDateTime(item.createdAt), item.createdByLabel,
        item.note ? `“${item.note}”` : ''].filter(Boolean).join(' · ')));
      if (canSave && Array.isArray(item.clauses)) {
        row.append(button('Bu sürümü düzenleyiciye al', { variant: 'ghost', onClick: () => {
          clauses = cloneClauses(item.clauses);
          paintEditor();
          app.toast(`Sürüm ${item.version} düzenleyicide; kaydederseniz yeni sürüm olur.`, 'info');
        } }));
      }
      box.append(row);
    }
    return box;
  }

  function tools(buttons) {
    const box = h('span', 'ak-szb-tools');
    for (const [label, title, onClick, disabled] of buttons) {
      const item = button(label, { variant: 'ghost', title, onClick });
      item.setAttribute('aria-label', title);
      item.disabled = Boolean(disabled);
      box.append(item);
    }
    box.hidden = !canSave;
    return box;
  }

  function paintEditor() {
    editorSlot.replaceChildren();
    clauses.forEach((clause, index) => {
      const box = h('div', 'ak-szb-clause');
      const headRow = h('div', 'ak-szb-clause-head');
      const title = h('input', 'kit-input');
      title.type = 'text';
      title.value = clause.title || '';
      title.placeholder = 'Başlık (isteğe bağlı, ör. TARAFLAR)';
      title.maxLength = 200;
      title.readOnly = !canSave;
      title.setAttribute('aria-label', `${index + 1}. madde başlığı`);
      title.addEventListener('focus', () => { focused = { clause: index, item: null, input: title }; });
      title.addEventListener('input', () => { clauses = setTitle(clauses, index, title.value); paintStatus(); });
      headRow.append(h('span', 'ak-szb-clause-no', `${index + 1}.`), title, tools([
        ['↑', `${index + 1}. maddeyi yukarı taşı`, () => { clauses = moveClause(clauses, index, -1); paintEditor(); },
          index === 0],
        ['↓', `${index + 1}. maddeyi aşağı taşı`, () => { clauses = moveClause(clauses, index, 1); paintEditor(); },
          index === clauses.length - 1],
        ['+ Madde', `${index + 1}. maddenin altına yeni madde`, () => {
          clauses = addClause(clauses, index + 1);
          paintEditor();
        }],
        ['Sil', `${index + 1}. maddeyi sil`, () => { clauses = removeClause(clauses, index); paintEditor(); }],
      ]));
      box.append(headRow);
      clause.items.forEach((text, itemIndex) => {
        const row = h('div', 'ak-szb-item');
        const area = h('textarea', 'kit-input');
        area.value = text;
        area.rows = Math.min(8, Math.max(2, Math.ceil(text.length / 90)));
        area.readOnly = !canSave;
        area.setAttribute('aria-label', `${index + 1}. madde, ${itemIndex + 1}. öğe`);
        area.addEventListener('focus', () => { focused = { clause: index, item: itemIndex, input: area }; });
        area.addEventListener('input', () => { clauses = setItem(clauses, index, itemIndex, area.value); paintStatus(); });
        row.append(area, tools([
          ['↑', 'Öğeyi yukarı taşı', () => { clauses = moveItem(clauses, index, itemIndex, -1); paintEditor(); },
            itemIndex === 0],
          ['↓', 'Öğeyi aşağı taşı', () => { clauses = moveItem(clauses, index, itemIndex, 1); paintEditor(); },
            itemIndex === clause.items.length - 1],
          ['+', 'Altına öğe ekle', () => { clauses = addItem(clauses, index, itemIndex + 1); paintEditor(); }],
          ['Sil', 'Öğeyi sil', () => { clauses = removeItem(clauses, index, itemIndex); paintEditor(); }],
        ]));
        box.append(row);
      });
      editorSlot.append(box);
    });
    focused = null;
    paintStatus();
  }

  function paintPreview() {
    previewSlot.replaceChildren();
    const model = previewModel(clauses, placeholders);
    if (!model.length) {
      previewSlot.append(h('span', 'ak-muted', 'Madde yok.'));
      return;
    }
    for (const clause of model) {
      if (clause.title) previewSlot.append(h('h4', '', `${clause.no}. ${clause.title}:`));
      clause.items.forEach((text, index) => {
        previewSlot.append(h('p', '', clause.title || index ? text : `${clause.no}. ${text}`));
      });
    }
  }

  function paintStatus() {
    const summary = changeSummary(saved, clauses);
    statusText.textContent = summary ? `Kaydedilmemiş: ${summary}` : 'Değişiklik yok';
    status.classList.toggle('dirty', Boolean(summary));
    reset.disabled = !summary;
    save.disabled = !summary || !canSave;
    const unknown = unknownPlaceholders(clauses, placeholders);
    warnings.replaceChildren();
    if (unknown.length && placeholders.length) {
      warnings.append(alertBox(`Tanınmayan yer tutucu: ${unknown.map((key) => `{${key}}`).join(', ')} — belgede `
        + 'olduğu gibi basılır. Yazımı yer tutucu listesindekiyle aynı olmalı.', 'warn'));
    }
    paintPreview();
  }

  async function submit() {
    const errors = validateClauses(clauses);
    if (errors.length) {
      warnings.replaceChildren(alertBox(errors.join(' '), 'bad'));
      app.toast('Şablonda düzeltilecek yer var.', 'bad');
      return;
    }
    const body = normalizeClauses(clauses);
    const result = await formDialog(app, {
      title: `${KIND_LABEL[kind]} — yeni sürüm`,
      description: 'Bu sürüm bundan sonra oluşturulan sözleşmelere uygulanır; mevcut sözleşmeler değişmez.',
      submitLabel: 'Kaydet',
      fields: [{ key: 'note', label: 'Değişiklik notu', maxLength: 200, wide: true,
        placeholder: 'ör. 7. madde kesinti oranı güncellendi' }],
      value: { note: '' },
      onSubmit: async (draft, { key }) => app.api.post(`${PATH}/${encodeURIComponent(kind)}`,
        { clauses: body, note: String(draft.note || '').trim() || undefined }, { key }),
    });
    if (!result) return;
    app.api.invalidate(PATH);
    app.toast(`${KIND_LABEL[kind]} şablonu kaydedildi; yeni sözleşmeler bu metinle çıkar.`, 'good');
    view.reload();
  }

  return { node, reload: () => view.reload() };
}
