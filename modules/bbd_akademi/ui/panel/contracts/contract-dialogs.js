// BBD Akademi — öğrencinin Sözleşmeler sekmesinin pencereleri (F-SZ, iş 80).
//
//   signDialog       "İmzalandı" (A10): sürüm, imza tarihi (≤ bugün), imzalanan türler, dosyadan tarama (PDF/JPG/PNG,
//                    sayfa başı ≤ 10 MB; Akademi tek PDF'e birleştirir), not.
//   attachDialog     imzasız ek tarama (A12): sürüm, ad, dosyalar.
//   clausesDialog    "Maddeleri düzenle" (A8): tür seç → madde listesi (başlık + öğeler; ekle/sil/sırala), MEB özel
//                    hususlar, GEREKÇE zorunlu → yeni sürüm, PDF'ler yeniden. İmzalıysa "yeniden imza gerektirir".
//   guardianDialog   "Velinin gördüğü" (K8): yalnız son imzalı sürümün imzalanan türleri + o sürümün taramaları.
//
// Yazmalar idempotenttir: pencere açıldığında tek anahtar (formDialog) ya da gövdeye bağlı anahtar; yüklenen tarama
// dosyası yeniden denemede tekrar yüklenmez (dosya → kimlik önbelleği).

import { button, dialogA11y, h } from '../../../ui-kit/kit.js';
import { alertBox, badge } from '../../../ui-kit/layout.js';
import { AkademiError, problem } from '../api.js';
import { fmtDate, formDialog, multiChips, num, selectBox, todayIst } from '../common.js';
import { KM_PDF_KINDS, openContractDocument, openContractFile, uploadScan } from './shared-contract.js';
import {
  CLAUSE_KINDS, DOCUMENT_KINDS, SCAN_MAX_MB, SCAN_TYPES, SIGNABLE_KINDS, SIGNATURE_SOURCES, clauseOps,
  cloneClauses, defaultSignKinds, guardianView, hashText, latestRevision, linesOf, needsResignWarning, reviseBody,
  scanFileProblem, signBody,
} from './shared-contract-logic.js';

// ------------------------------------------------------------------ dosya seçici (çoklu)

/** Çoklu tarama seçici: sürükle-bırak ya da seç; sırayla listelenir, × ile çıkarılır. */
export function scanPicker({ onChange } = {}) {
  const node = h('div', 'ak-ct-scans');
  const drop = h('label', 'ak-drop');
  const input = h('input');
  input.type = 'file';
  input.multiple = true;
  input.accept = SCAN_TYPES.map((ext) => `.${ext}`).join(',');
  input.hidden = true;
  drop.append(input, h('span', 'ak-drop-text', 'Taranmış sayfaları seçin ya da buraya bırakın'),
    h('span', 'ak-drop-hint', `PDF, JPG, PNG · sayfa başı en çok ${SCAN_MAX_MB} MB · sıra korunur`));
  const list = h('ol', 'ak-ct-scan-list');
  const error = h('div', 'ak-drop-error');
  error.setAttribute('role', 'alert');
  node.append(drop, list, error);
  let files = [];
  const paint = () => {
    list.replaceChildren();
    files.forEach((file, index) => {
      const row = h('li', 'ak-ct-scan');
      const remove = button('×', { variant: 'ghost', onClick: () => { files = files.filter((_, at) => at !== index); paint(); } });
      remove.setAttribute('aria-label', `${file.name} dosyasını çıkar`);
      row.append(h('span', '', file.name), h('small', 'ak-muted', `${num(Math.ceil((file.size || 0) / 1024))} KB`), remove);
      list.append(row);
    });
    onChange?.([...files]);
  };
  const take = (picked) => {
    const problems = [];
    for (const file of picked) {
      const issue = scanFileProblem(file);
      if (issue) problems.push(issue); else files.push(file);
    }
    error.textContent = problems.join(' ');
    paint();
  };
  input.addEventListener('change', () => { take([...(input.files || [])]); input.value = ''; });
  drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    take([...(event.dataTransfer?.files || [])]);
  });
  return { node, files: () => [...files] };
}

/** Dosyaları sırayla yükler; aynı dosya (nesne) ikinci denemede yeniden yüklenmez. */
async function uploadAll(app, files, contractId, cache, onStep) {
  const ids = [];
  for (const [index, file] of files.entries()) {
    if (!cache.has(file)) {
      onStep?.(`Yükleniyor ${index + 1} / ${files.length}: ${file.name}`);
      cache.set(file, await uploadScan(app, file, contractId)); // eslint-disable-line no-await-in-loop
    }
    ids.push(cache.get(file));
  }
  return ids;
}

const revisionOptions = (detail) => [...(detail.revisions || [])].sort((a, b) => b.no - a.no)
  .map((item) => ({ value: String(item.no), label: `Sürüm ${item.no} · ${fmtDate(String(item.createdAt || '').slice(0, 10))}`
    + `${item.signature ? ' · imzalı' : ''}${item.reason ? ` · ${item.reason}` : ''}` }));

// ------------------------------------------------------------------ İmzalandı (A10)

/** @returns {Promise<object|null>} güncel ContractDetail ya da vazgeçildiyse null */
export function signDialog(app, detail, { revisionNo = null } = {}) {
  const latest = latestRevision(detail);
  const start = revisionNo || latest?.no || 1;
  const cache = new Map();
  let files = [];
  const kindsOf = (no) => defaultSignKinds((detail.revisions || []).find((item) => item.no === Number(no)) || {});
  let chips = null;
  return formDialog(app, {
    title: `İmzalandı — Sözleşme ${detail.number || ''}`,
    description: 'Velinin ıslak imzalı nüshasını işaretleyin. İmzalanan türler veliye uygulamada görünür '
      + '(taslak ve imza bekleyen sürüm görünmez). Taranmış sayfalar isteğe bağlıdır.',
    wide: true,
    value: { revisionNo: String(start), signedOn: todayIst(), note: '', kinds: kindsOf(start) },
    fields: [
      { key: 'revisionNo', label: 'Sürüm', type: 'select', options: revisionOptions(detail) },
      { key: 'signedOn', label: 'İmza tarihi', type: 'date', required: true },
      { key: 'kinds', label: 'İmzalanan belgeler', type: 'custom', required: true, render: (draft, set) => {
        chips = multiChips(SIGNABLE_KINDS.map((kind) => ({ value: kind, label: DOCUMENT_KINDS[kind] })),
          draft.kinds, (value) => set(value));
        let shown = draft.revisionNo;
        return { node: chips.node, update(next) {
          if (next.revisionNo === shown) return;
          shown = next.revisionNo;
          const kinds = kindsOf(next.revisionNo);
          chips.set(kinds);
          set(kinds);
        } };
      } },
      { key: 'scans', label: 'Taranmış imzalı nüsha (isteğe bağlı)', type: 'custom', render: () =>
        scanPicker({ onChange: (list) => { files = list; } }).node },
      { key: 'note', label: 'Not', maxLength: 300 },
    ],
    submitLabel: 'İmzalandı olarak kaydet',
    onSubmit: async (draft, { key }) => {
      const prepared = signBody({ revisionNo: Number(draft.revisionNo), signedOn: draft.signedOn, kinds: draft.kinds || [],
        note: draft.note }, { today: todayIst() });
      if (prepared.error) throw new AkademiError({ message: prepared.error });
      const uploads = files.length ? await uploadAll(app, files, detail.id, cache) : [];
      const { body } = signBody({ revisionNo: Number(draft.revisionNo), signedOn: draft.signedOn, kinds: draft.kinds || [],
        uploads, note: draft.note }, { today: todayIst() });
      return app.api.post(`/v1/admin/contracts/${detail.id}/signatures`, body, { key });
    },
  });
}

// ------------------------------------------------------------------ ek tarama (A12)

export function attachDialog(app, detail) {
  const latest = latestRevision(detail);
  const cache = new Map();
  let files = [];
  return formDialog(app, {
    title: `Tarama ekle — Sözleşme ${detail.number || ''}`,
    description: 'İmzasız ek (ör. kimlik fotokopisi, ek protokol). İmzalı nüsha için "İmzalandı"yı kullanın.',
    wide: true,
    value: { revisionNo: String(latest?.no || 1), label: '' },
    fields: [
      { key: 'revisionNo', label: 'Sürüm', type: 'select', options: revisionOptions(detail) },
      { key: 'label', label: 'Ad', maxLength: 80, placeholder: 'Ek protokol' },
      { key: 'scans', label: 'Dosyalar', type: 'custom', required: true, render: (_draft, set) =>
        scanPicker({ onChange: (list) => { files = list; set(list.length ? list.length : null); } }).node },
    ],
    submitLabel: 'Ekle',
    onSubmit: async (draft, { key }) => {
      if (!files.length) throw new AkademiError({ message: 'En az bir dosya seçin.' });
      const fileIds = await uploadAll(app, files, detail.id, cache);
      const body = { revisionNo: Number(draft.revisionNo), fileIds };
      if (String(draft.label || '').trim()) body.label = String(draft.label).trim();
      return app.api.post(`/v1/admin/contracts/${detail.id}/attachments`, body, { key });
    },
  });
}

// ------------------------------------------------------------------ Maddeleri düzenle (A8)

/** Tek türün madde listesi düzenleyicisi. `get()` güncel listeyi verir. */
export function clauseEditor(initial = []) {
  let clauses = cloneClauses(initial);
  const node = h('div', 'ak-ct-clauses');
  const iconButton = (text, label, onClick, disabled = false) => {
    const item = button(text, { variant: 'ghost', onClick, disabled });
    item.setAttribute('aria-label', label);
    item.title = label;
    return item;
  };
  const apply = (next, focus = null) => {
    clauses = next;
    paint();
    if (focus) node.querySelector(focus)?.focus();
  };
  function paint() {
    node.replaceChildren();
    if (!clauses.length) node.append(h('div', 'ak-muted', 'Madde yok.'));
    clauses.forEach((clause, index) => {
      const box = h('div', 'ak-ct-clause');
      const head = h('div', 'ak-ct-clause-head');
      const title = h('input', 'kit-input ak-ct-clause-title');
      title.type = 'text';
      title.maxLength = 160;
      title.placeholder = 'Başlık (isteğe bağlı)';
      title.value = clause.title || '';
      title.dataset.clause = String(index);
      title.setAttribute('aria-label', `${index + 1}. madde başlığı`);
      title.addEventListener('input', () => { clauses = clauseOps.setTitle(clauses, index, title.value); });
      head.append(h('b', 'ak-ct-clause-no', `${index + 1}.`), title,
        iconButton('↑', `${index + 1}. maddeyi yukarı al`, () => apply(clauseOps.moveClause(clauses, index, index - 1)), index === 0),
        iconButton('↓', `${index + 1}. maddeyi aşağı al`, () => apply(clauseOps.moveClause(clauses, index, index + 1)),
          index === clauses.length - 1),
        iconButton('Sil', `${index + 1}. maddeyi sil`, () => apply(clauseOps.removeClause(clauses, index))));
      box.append(head);
      clause.items.forEach((item, slot) => {
        const row = h('div', 'ak-ct-item');
        const text = h('textarea', 'kit-input ak-ct-item-text');
        text.rows = Math.min(6, Math.max(2, Math.ceil(String(item).length / 90)));
        text.value = item;
        text.dataset.item = `${index}.${slot}`;
        text.setAttribute('aria-label', `${index + 1}. madde, ${slot + 1}. öğe`);
        text.addEventListener('input', () => { clauses = clauseOps.setItem(clauses, index, slot, text.value); });
        row.append(text, h('span', 'ak-ct-item-tools'));
        row.lastChild.append(
          iconButton('↑', 'Öğeyi yukarı al', () => apply(clauseOps.moveItem(clauses, index, slot, slot - 1)), slot === 0),
          iconButton('↓', 'Öğeyi aşağı al', () => apply(clauseOps.moveItem(clauses, index, slot, slot + 1)),
            slot === clause.items.length - 1),
          iconButton('×', 'Öğeyi sil', () => apply(clauseOps.removeItem(clauses, index, slot))));
        box.append(row);
      });
      box.append(button('+ Öğe', { variant: 'ghost',
        onClick: () => apply(clauseOps.addItem(clauses, index), `[data-item="${index}.${clause.items.length}"]`) }));
      node.append(box);
    });
    node.append(button('+ Madde ekle', { onClick: () => apply(clauseOps.addClause(clauses),
      `[data-clause="${clauses.length}"]`) }));
  }
  paint();
  return { node, get: () => cloneClauses(clauses) };
}

/** @returns {Promise<object|null>} yeni sürümlü ContractDetail ya da vazgeçildiyse null */
export function clausesDialog(app, detail) {
  return new Promise((resolve) => {
    const original = {};
    for (const kind of CLAUSE_KINDS) original[kind] = cloneClauses(detail.clauses?.[kind] || []);
    const editors = {};
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog wide ak-ct-editor');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', `Maddeleri düzenle — Sözleşme ${detail.number || ''}`),
      h('p', 'kit-dialog-text', 'Yalnız bu sözleşmenin metni değişir (şablon değişmez). Kaydedince yeni sürüm oluşur ve '
        + 'belgeler bu metinle yeniden üretilir.'));
    if (needsResignWarning(detail)) {
      card.append(alertBox('Bu sözleşmenin imzalı sürümü var: bu değişiklik YENİDEN İMZA gerektirir. Veli yeni sürümü '
        + 'imzalanana dek eski imzalı sürümü görmeye devam eder.', 'warn'));
    }
    const kinds = [...CLAUSE_KINDS, 'special'];
    const label = (kind) => (kind === 'special' ? 'MEB özel hususlar' : DOCUMENT_KINDS[kind]);
    let current = 'registration';
    const picker = selectBox(kinds.map((kind) => ({ value: kind, label: label(kind) })), current,
      (value) => show(value), { label: 'Belge' });
    const slot = h('div', 'ak-ct-editor-body');
    const special = h('textarea', 'kit-input ak-ct-special');
    special.rows = 8;
    special.value = (detail.specialClauses || []).join('\n');
    special.setAttribute('aria-label', 'MEB özel hususlar (her satır bir madde)');
    function show(kind) {
      current = kind;
      if (kind === 'special') {
        slot.replaceChildren(h('div', 'ak-muted', 'Her satır bir madde (MEB sözleşmesi "Özel Hususlar").'), special);
        return;
      }
      editors[kind] ||= clauseEditor(original[kind]);
      slot.replaceChildren(editors[kind].node);
    }
    const reason = h('input', 'kit-input');
    reason.type = 'text';
    reason.maxLength = 200;
    reason.placeholder = 'Gerekçe (zorunlu) — ör. velinin isteğiyle 5. madde değişti';
    reason.setAttribute('aria-label', 'Gerekçe');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    const key = `km-ct-rev-${detail.id}-${Date.now().toString(36)}`;
    let release = () => {};
    const close = (result) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(result);
    };
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      const layers = document.querySelectorAll('.kit-overlay');
      if (layers[layers.length - 1] === overlay) close(null);
    };
    const save = button('Yeni sürümü kaydet', { variant: 'primary', onClick: async () => {
      const edited = {};
      for (const [kind, editor] of Object.entries(editors)) edited[kind] = editor.get();
      const prepared = reviseBody({ reason: reason.value, original, edited, specialOriginal: detail.specialClauses || [],
        specialEdited: linesOf(special.value) });
      if (prepared.error) { error.textContent = prepared.error; return; }
      save.disabled = true;
      save.textContent = 'Kaydediliyor…';
      error.textContent = '';
      try {
        close(await app.api.post(`/v1/admin/contracts/${detail.id}/revisions`, prepared.body,
          { key: `${key}-${hashText(JSON.stringify(prepared.body))}` }));
      } catch (failure) {
        save.disabled = false;
        save.textContent = 'Yeni sürümü kaydet';
        error.textContent = problem(failure);
      }
    } });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), save);
    const tools = h('div', 'ak-ct-editor-tools');
    tools.append(h('span', 'kit-field-label', 'Belge'), picker.node);
    card.append(tools, slot, h('span', 'kit-field-label', 'Gerekçe'), reason, error, actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    show(current);
    picker.node.focus?.();
  });
}

// ------------------------------------------------------------------ Velinin gördüğü (K8)

export function guardianDialog(app, detail) {
  const view = guardianView(detail);
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog ak-dialog ak-ct-guardian');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.append(h('h3', 'kit-dialog-title', 'Velinin gördüğü'));
  const payer = detail.payerGuardian?.name ? `${detail.payerGuardian.name} (ödeme sorumlusu)` : 'ödeme sorumlusu veli';
  if (!view.revisionNo) {
    card.append(alertBox(`Henüz imzalı sürüm yok: ${payer} bu sözleşmeyi uygulamada GÖRMEZ. "İmzalandı" işaretlenince görür.`, 'info'));
  } else {
    card.append(h('p', 'kit-dialog-text', `${payer} uygulamada (Finans → Sözleşmelerim) yalnız imzaladığını görür: `
      + `Sürüm ${view.revisionNo} · imza ${fmtDate(view.signedOn)} · ${SIGNATURE_SOURCES[view.source] || ''}. `
      + 'Ödeme planı ve yasal uyarı sözleşmeler ekranında yer almaz; öğrenci hiçbirini görmez.'));
    const list = h('div', 'ak-ct-docs');
    for (const kind of view.kinds) {
      const row = h('div', 'ak-ct-doc');
      row.append(h('span', '', DOCUMENT_KINDS[kind]), badge('İmzalı', 'good'), h('span', 'kit-spacer'),
        button('Aç', { variant: 'ghost', onClick: (event) => openContractDocument(app, detail, kind,
          { revision: view.revisionNo, opener: event?.currentTarget || null }) }));
      list.append(row);
    }
    for (const item of view.attachments) {
      const row = h('div', 'ak-ct-doc');
      row.append(h('span', '', item.label || 'Taranmış nüsha'), badge(`${num(item.pageCount || 0)} sayfa`, 'dim'),
        h('span', 'kit-spacer'), button('Aç', { variant: 'ghost', onClick: () => openAttachment(app, detail, item) }));
      list.append(row);
    }
    card.append(list);
  }
  let release = () => {};
  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    release();
  };
  const onKey = (event) => {
    if (event.key !== 'Escape') return;
    const layers = document.querySelectorAll('.kit-overlay');
    if (layers[layers.length - 1] === overlay) close();
  };
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Kapat', { variant: 'primary', onClick: close }));
  card.append(actions);
  overlay.append(card);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  app.root.append(overlay);
  release = dialogA11y(card);
  return { close };
}

/** Ek (tarama) dosyasını açar (A15). */
export async function openAttachment(app, detail, attachment, opener = null) {
  try {
    const file = await app.api.get(`/v1/admin/contracts/${detail.id}/attachments/${attachment.id}/file`);
    return openContractFile(app, { kind: KM_PDF_KINDS.attachment, url: file?.url,
      name: file?.fileName || `${detail.number || 'sozlesme'}-ek`, opener });
  } catch (failure) {
    app.toast(problem(failure, 'Ek açılamadı.'), 'bad');
    return null;
  }
}
