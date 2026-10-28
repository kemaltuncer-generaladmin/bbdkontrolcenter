// BBD Akademi — Sözleşme ayarları (Sözleşmeler → "Sözleşme ayarları"; Akademi kurum ayarları `contracts.*`).
//
// Kurum unvanı, MEB okul adı, yetkili mahkeme ili, banka hesapları (IBAN anlık doğrulama), sözleşme numarası
// (önek + sonraki sayı, KULLANICI 28.10: "BBD1-1000 den başlat sözleşmeleri"), eğitim yılı başlangıç ayı, kayıt seti,
// nüsha varsayılanları, veli görünürlüğü, imza etiketleri. Ayrılma kesinti oranı finans ayarıdır (çoğaltılmaz).
//
// Okuma `admin_getSettings`, yazma `admin_patchSettings` (KM izni: kurum ayarları). Yalnız değişen anahtar gider
// (`settings-logic.js` → `settingsPatch`).

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, card } from '../../../ui-kit/layout.js';
import { actionButton, loader, problem } from '../common.js';
import { DOCUMENT_KINDS } from './documents-logic.js';
import {
  KIND_KEYS, MONTHS, VISIBILITY, dirtyCount, formatIban, ibanError, nextNumberPreview, settingsDraft, settingsPatch,
  validateSettings,
} from './settings-logic.js';

const KIND_LABEL = Object.fromEntries(DOCUMENT_KINDS.map((kind) => [kind.key, kind.label]));

export function contractSettingsTab(app) {
  loadStyles(import.meta.url, '../admin/settings.css');   // kaydet şeridi + ayar ızgarası Kurum ayarlarıyla aynı
  const node = h('div', 'ak-stack ak-set ak-szb-settings');
  let original = null;          // sunucunun `contracts` görünümü
  let draft = null;
  let errorsShown = false;
  const errorSlots = new Map();

  const view = loader(async () => app.api.getCached('/v1/admin/settings'), (data) => render(data),
    { rows: 6, columns: 3 });
  node.append(view.node);

  // ------------------------------------------------------------ yardımcılar
  function field(label, control, { hint = '', error = '', wide = false } = {}) {
    const wrap = h('label', `kit-field${wide ? ' kit-field-wide' : ''}`);
    wrap.append(h('span', 'kit-field-label', label), control);
    if (hint) wrap.append(h('span', 'kit-field-hint', hint));
    if (error) {
      const slot = h('span', 'kit-field-error');
      errorSlots.set(error, slot);
      wrap.append(slot);
    }
    return wrap;
  }

  function input(value, onInput, { maxLength = 200, placeholder = '', className = '', mode = '' } = {}) {
    const item = h('input', `kit-input${className ? ` ${className}` : ''}`);
    item.type = 'text';
    item.value = value ?? '';
    item.maxLength = maxLength;
    if (placeholder) item.placeholder = placeholder;
    if (mode) item.inputMode = mode;
    item.addEventListener('input', () => { onInput(item.value, item); paint(); });
    return item;
  }

  function grid(...children) {
    const box = h('div', 'kit-form-grid ak-set-grid');
    box.append(...children);
    return box;
  }

  // ------------------------------------------------------------ çizim
  let saveBar = null;
  function render(data) {
    original = data?.contracts && typeof data.contracts === 'object' ? data.contracts : null;
    draft = settingsDraft(original);
    errorSlots.clear();
    errorsShown = false;
    const parts = [];
    saveBar = bar();
    parts.push(saveBar.node);
    if (!original) {
      parts.push(alertBox('Akademi bu sürümde sözleşme ayarlarını henüz bildirmiyor; aşağıdaki değerler varsayılandır. '
        + 'Akademi güncellenince kaydedebilirsiniz.', 'info'));
    }
    parts.push(identityCard(), numberCard(), banksCard(), kitCard(), visibilityCard(), labelsCard(), deductionNote());
    paint();
    return parts;
  }

  function identityCard() {
    return card('Belgedeki kurum bilgileri', grid(
      field('Kurum unvanı (belgelerde)', input(draft.legalName, (value) => { draft.legalName = value; },
        { placeholder: 'ör. BBD Özel Öğretim Kursu' }), { error: 'legalName', wide: true,
        hint: 'Sözleşme ve senetlerde "kurum" olarak yazan tam unvan. Boşsa kurum adı kullanılır.' }),
      field('MEB formundaki okul adı', input(draft.mebSchoolName, (value) => { draft.mebSchoolName = value; }),
        { error: 'mebSchoolName', hint: 'MEB Kayıt Sözleşmesi\'ndeki "Okulun adı".' }),
      field('Yetkili mahkeme ili', input(draft.courtCity, (value) => { draft.courtCity = value; },
        { maxLength: 40, placeholder: 'KONYA' }), { error: 'courtCity',
        hint: 'Senetlerdeki "… mahkemeleri yetkilidir" ili; büyük harfle basılır.' }),
    ), 'Belgelerin başlığı ve imza alanları bu bilgilerle basılır. Logo ve renk: Kurum ayarları → Tema ve logo.');
  }

  function numberCard() {
    const preview = h('span', 'ak-szb-preview-no');
    const paintPreview = () => { preview.textContent = `Sonraki: ${nextNumberPreview(draft.numberPrefix, draft.nextNumber)}`; };
    paintPreview();
    const prefix = input(draft.numberPrefix, (value) => { draft.numberPrefix = value; paintPreview(); },
      { maxLength: 10, placeholder: 'BBD1-' });
    const next = input(String(draft.nextNumber ?? ''), (value) => {
      draft.nextNumber = value.replace(/\D/g, '') === '' ? '' : Number(value.replace(/\D/g, ''));
      paintPreview();
    }, { maxLength: 9, mode: 'numeric', placeholder: '1000' });
    const body = h('div', 'ak-stack');
    body.append(grid(
      field('Numara öneki', prefix, { error: 'numberPrefix', hint: 'ör. BBD1-' }),
      field('Sonraki sözleşme no', next, { error: 'nextNumber',
        hint: 'Yeni sözleşme bu sayıyı alır, sonra birer artar.' }),
    ), preview);
    return card('Sözleşme numarası', body, 'Numara = önek + sayı (BBD1-1000, BBD1-1001 …). Mevcut öğrencilerin eski basılı '
      + 'sözleşmeleri kendi eski numarasıyla (ör. 72128) girilir ve sayacı etkilemez.');
  }

  function banksCard() {
    const list = h('div', 'ak-szb-banks');
    const paintList = () => {
      list.replaceChildren();
      for (const key of [...errorSlots.keys()]) if (key.startsWith('bank.')) errorSlots.delete(key);
      if (!draft.bankAccounts.length) {
        list.append(h('div', 'ak-muted', 'Banka hesabı yok. Ödeme planında velinin ödeme yapacağı hesaplar listelenir.'));
      }
      draft.bankAccounts.forEach((account, index) => {
        const row = h('div', 'ak-szb-bank');
        const iban = input(account.iban, (value, item) => {
          account.iban = value;
          const problemText = ibanError(value);
          item.classList.toggle('ok', !problemText);
          item.classList.toggle('bad', Boolean(problemText) && value.replace(/\s/g, '').length >= 26);
        }, { maxLength: 34, placeholder: 'TR00 0000 0000 0000 0000 0000 00', className: 'ak-szb-iban' });
        iban.classList.toggle('ok', !ibanError(account.iban));
        iban.addEventListener('blur', () => { iban.value = formatIban(iban.value); account.iban = iban.value; paint(); });
        const remove = button('Sil', { variant: 'ghost', title: 'Bu hesabı listeden çıkar', onClick: () => {
          draft.bankAccounts.splice(index, 1);
          paintList();
          paint();
        } });
        row.append(
          field('Banka', input(account.bank, (value) => { account.bank = value; }, { maxLength: 80 }),
            { error: `bank.${index}.bank` }),
          field('Hesap sahibi', input(account.holder, (value) => { account.holder = value; }, { maxLength: 120 }),
            { error: `bank.${index}.holder` }),
          field('Şube', input(account.branch, (value) => { account.branch = value; }, { maxLength: 80 })),
          field('IBAN', iban, { error: `bank.${index}.iban` }),
          remove,
        );
        list.append(row);
      });
      if (errorsShown) showErrors();
    };
    paintList();
    const add = button('Hesap ekle', { onClick: () => {
      draft.bankAccounts.push({ bank: '', holder: '', branch: '', iban: '' });
      paintList();
      paint();
      list.querySelector('.ak-szb-bank:last-child input')?.focus();
    } });
    const body = h('div', 'ak-stack');
    body.append(list, add);
    return card('Banka hesapları', body, 'Ödeme planında ve sözleşmede basılır. IBAN yazarken sağlaması anında denetlenir.');
  }

  function kitCard() {
    const kinds = h('div', 'ak-szb-copies-row');
    for (const key of KIND_KEYS) {
      const row = h('div', 'ak-szb-kind on');
      const label = h('label', 'ak-szb-check');
      const box = h('input');
      box.type = 'checkbox';
      box.checked = draft.registrationKinds.includes(key);
      box.addEventListener('change', () => {
        draft.registrationKinds = box.checked
          ? [...new Set([...draft.registrationKinds, key])]
          : draft.registrationKinds.filter((item) => item !== key);
        paint();
      });
      label.append(box, h('span', '', KIND_LABEL[key]));
      const copies = h('select', 'kit-select ak-szb-copies');
      copies.setAttribute('aria-label', `${KIND_LABEL[key]} nüsha varsayılanı`);
      for (const value of [1, 2, 3]) {
        const option = h('option', '', `${value} nüsha`);
        option.value = String(value);
        copies.append(option);
      }
      copies.value = String(draft.defaultCopies[key] || 1);
      copies.addEventListener('change', () => { draft.defaultCopies[key] = Number(copies.value); paint(); });
      row.append(label, copies);
      kinds.append(row);
    }
    const kindsError = h('span', 'kit-field-error');
    errorSlots.set('registrationKinds', kindsError);
    const month = h('select', 'kit-select');
    MONTHS.forEach((name, index) => {
      const option = h('option', '', name);
      option.value = String(index + 1);
      month.append(option);
    });
    month.value = String(draft.academicYearStartMonth || 9);
    month.addEventListener('change', () => { draft.academicYearStartMonth = Number(month.value); paint(); });
    const body = h('div', 'ak-stack');
    body.append(h('div', 'kit-field-label', 'Kayıt setindeki belgeler (işaretli) ve varsayılan nüsha'), kinds,
      kindsError, grid(field('Eğitim yılı başlangıç ayı', month, { error: 'academicYearStartMonth',
        hint: 'Yeni sözleşmede eğitim yılı bu aya göre önerilir (Eylül → 2026-2027).' })));
    return card('Kayıt seti ve nüshalar', body, '"Kaydet ve yazdır" kayıt setini tek PDF basar; "Belge çıkar" nüsha '
      + 'sayılarını buradan önerir.');
  }

  function visibilityCard() {
    const body = h('div', 'ak-stack');
    const cards = [];
    for (const [value, [label, hint]] of Object.entries(VISIBILITY)) {
      const row = h('label', 'ak-szb-radio-card');
      const radio = h('input');
      radio.type = 'radio';
      radio.name = 'ak-szb-visibility';
      radio.value = value;
      radio.checked = draft.guardianVisibility === value;
      radio.addEventListener('change', () => {
        draft.guardianVisibility = value;
        for (const item of cards) item.row.classList.toggle('on', item.value === value);
        paint();
      });
      const text = h('span', 'ak-cell-stack');
      text.append(h('b', '', label), h('small', '', hint));
      row.append(radio, text);
      row.classList.toggle('on', radio.checked);
      cards.push({ row, value });
      body.append(row);
    }
    body.append(h('p', 'ak-muted', 'Veli yalnız İMZALADIĞI sürümü görür (kayıt sözleşmesi, MEB sözleşmesi, senetler ve '
      + 'taranmış nüshalar). Öğrenci sözleşme ve paraya dair hiçbir şey görmez.'));
    return card('Velinin gördüğü', body);
  }

  function labelsCard() {
    const set = (key) => (value) => { draft.signatureLabels[key] = value; };
    return card('İmza alanı etiketleri', grid(
      field('Öğrenci', input(draft.signatureLabels.student, set('student'), { maxLength: 40 }),
        { error: 'signature.student' }),
      field('Veli', input(draft.signatureLabels.guardian, set('guardian'), { maxLength: 40 }),
        { error: 'signature.guardian' }),
      field('Kurum', input(draft.signatureLabels.institution, set('institution'), { maxLength: 40 }),
        { error: 'signature.institution' }),
    ), 'Belgelerin sonundaki imza kutularının başlıkları.');
  }

  function deductionNote() {
    const box = h('div', 'ak-szb-warn');
    box.append(h('span', '', 'Ayrılmada kurum kesintisi (%) sözleşmede de yazar; değeri Kurum ayarları → Kurum '
      + 'finansı\'ndadır (tek yerde tutulur). '));
    if (app.canOpen?.('settings')) {
      box.append(button('Kurum finansı ayarını aç', { variant: 'ghost',
        onClick: () => app.go('settings', { tab: 'institution' }) }));
    }
    return box;
  }

  // ------------------------------------------------------------ kaydet şeridi
  function bar() {
    const wrap = h('div', 'ak-set-savebar');
    const text = h('span', 'ak-set-savebar-text', 'Değişiklik yok');
    const reset = button('Değişiklikleri geri al', { variant: 'ghost', onClick: () => view.reload() });
    const save = actionButton(app, 'admin_patchSettings', 'Kaydet', { variant: 'primary', onClick: () => submit() });
    const blocked = save.dataset.blocked === '1' || save.hidden;
    wrap.append(text, h('span', 'kit-spacer'), reset, save);
    let busy = false;
    return {
      node: wrap,
      paint(count) {
        text.textContent = count ? `${count} kaydedilmemiş değişiklik` : 'Değişiklik yok';
        wrap.classList.toggle('dirty', count > 0);
        reset.disabled = busy || !count;
        if (!blocked) {
          save.disabled = busy || !count;
          save.textContent = busy ? 'Kaydediliyor…' : 'Kaydet';
        }
      },
      busy(flag) { busy = flag; },
    };
  }

  function showErrors() {
    const errors = validateSettings(draft);
    for (const [key, slot] of errorSlots) slot.textContent = errors[key] || '';
    return errors;
  }

  function paint() {
    if (!draft || !saveBar) return;
    saveBar.paint(dirtyCount(original, draft));
    if (errorsShown) showErrors();
  }

  async function submit() {
    errorsShown = true;
    const errors = showErrors();
    if (Object.keys(errors).length) {
      app.toast('İşaretli alanları düzeltin.', 'bad');
      return;
    }
    const patch = settingsPatch(original, draft);
    if (!patch) return;
    saveBar.busy(true);
    paint();
    try {
      const next = await app.api.patch('/v1/admin/settings', patch);
      app.api.invalidate('/v1/admin/settings');
      app.toast('Sözleşme ayarları kaydedildi.', 'good');
      view.node.replaceChildren(...render(next && typeof next === 'object' && next.contracts ? next
        : { contracts: { ...(original || {}), ...patch.contracts } }));
    } catch (failure) {
      app.toast(problem(failure), 'bad');
      saveBar.busy(false);
      paint();
    }
  }

  return { node, reload: () => view.reload() };
}
