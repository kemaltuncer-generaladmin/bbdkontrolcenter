// "Kimlik ve adres (sözleşme için)" — öğrenci kartının bölümü (göç 014, iş 80-km-kimlik).
//
// Kullanıcı: "eksikleri de ekleriz öğrenciler panelinde düzenleriz". Kayıt sözleşmesi,
// MEB formu ve senet BBD Akademi'de bu bilgilerle basılır; Akademi KM'yi 1–3 sn'de aynalar.
//
//   GET /api/bbd_students/students/{id}/identity   bbd_students.view (TC tam yalnız .manage)
//   PUT /api/bbd_students/students/{id}/identity   bbd_students.manage — yalnız değişen alanlar
//
// KLAVYEYLE HIZLI: Tab ile alandan alana, Enter (ya da Ctrl+Enter) kaydeder; TC / e-posta /
// telefon / tarih yazarken anında denetlenir. Kural `identity-model.js` (Node testli).
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { button } from '../../ui-kit/kit.js';
import * as M from './identity-model.js';

const BASE = '/api/bbd_students';
const enc = encodeURIComponent;

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

let seq = 0;

/** Il / ilçe öneri listeleri (belgede bir kez). */
function datalists() {
  if (!document.getElementById('st-id-districts')) {
    const districts = h('datalist');
    districts.id = 'st-id-districts';
    for (const name of M.KONYA_DISTRICTS) districts.append(new Option(name, name));
    const cities = h('datalist');
    cities.id = 'st-id-cities';
    for (const name of M.CITIES) cities.append(new Option(name, name));
    document.body.append(districts, cities);
  }
}

/**
 * @param {HTMLElement} box   kartın gövdesi
 * @param {object} options    api, kantinId, can(izin), toast?, onSaved?(sonuç)
 * @returns {{reload: () => Promise<void>, destroy: () => void, dirty: () => boolean}}
 */
export function mountIdentity(box, options) {
  const { api, kantinId } = options;
  const can = options.can || (() => true);
  const toast = options.toast || (() => {});
  const uid = `idn${(seq += 1)}`;
  let view = null;
  let form = null;
  let destroyed = false;
  let saving = false;
  const errorsByKey = new Map();
  datalists();

  const summary = h('p', 'st-id-summary');
  summary.setAttribute('role', 'status');
  const content = h('div', 'st-id-body');
  const errors = h('ul', 'st-errors');
  errors.setAttribute('role', 'alert');
  const save = button('Kimlik bilgilerini kaydet', { variant: 'primary', onClick: () => submit() });
  const dirtyNote = h('span', 'st-dirty', 'Kaydedilmedi');
  const actions = h('div', 'st-rec-group-tools');
  actions.append(dirtyNote, save);
  box.append(summary, content, errors, actions);
  content.append(h('p', 'st-hint', 'Kimlik bilgileri okunuyor…'));

  async function reload() {
    try {
      const fresh = await api(`${BASE}/students/${enc(kantinId)}/identity`);
      if (destroyed) return;
      view = fresh;
      form = M.toForm(fresh);
      paint();
    } catch (failure) {
      if (destroyed) return;
      content.replaceChildren(h('p', 'st-status bad', `Kimlik bilgileri okunamadı: ${failure?.message || 'bilinmeyen hata'}`),
        button('Tekrar dene', { onClick: () => reload() }));
    }
  }

  const editable = () => can('bbd_students.manage');

  function paintSummary() {
    const missing = view?.missing || [];
    summary.textContent = M.missingLine(missing);
    summary.className = `st-id-summary ${missing.length ? 'st-lc-warn' : 'st-id-ok'}`;
  }

  /** Tek giriş alanı: etiket + girdi + alan hatası (aria-describedby). */
  function input(label, get, set, opts = {}) {
    const id = `${uid}-${opts.key}`;
    const wrap = h('div', `st-field${opts.wide ? ' st-id-wide' : ''}`);
    const caption = h('label', 'st-label', label);
    caption.htmlFor = id;
    let node;
    if (opts.choices) {
      node = h('select', 'st-input st-select');
      for (const [value, text] of opts.choices) node.append(new Option(text, value));
    } else {
      node = h('input', 'st-input');
      node.type = 'text';
      node.autocomplete = 'off';
      if (opts.placeholder) node.placeholder = opts.placeholder;
      if (opts.inputMode) node.inputMode = opts.inputMode;
      if (opts.list) node.setAttribute('list', opts.list);
      if (opts.maxLength) node.maxLength = opts.maxLength;
    }
    node.id = id;
    node.value = get() ?? '';
    node.disabled = !editable();
    const error = h('span', 'st-field-error');
    error.id = `${id}-err`;
    node.setAttribute('aria-describedby', error.id);
    errorsByKey.set(opts.key, { node, error });
    node.addEventListener(opts.choices ? 'change' : 'input', () => {
      set(node.value);
      if (opts.check) showError(opts.key, opts.check(node.value));
      paintDirty();
    });
    if (opts.check && !opts.choices) {
      node.addEventListener('blur', () => showError(opts.key, opts.check(node.value)));
    }
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });
    wrap.append(caption, node, error);
    if (opts.extra) wrap.append(opts.extra);
    return wrap;
  }

  function showError(key, message) {
    const entry = errorsByKey.get(key);
    if (!entry) return;
    entry.error.textContent = message || '';
    if (message) entry.node.setAttribute('aria-invalid', 'true');
    else entry.node.removeAttribute('aria-invalid');
  }

  /** Açık adres + ilçe + il (il/ilçe serbest metin, öneri Konya önce). */
  function addressFields(prefix, holder, label) {
    const grid = h('div', 'st-grid st-id-address');
    const value = () => holder();
    grid.append(
      input(`${label} — açık adres`, () => value().line, (text) => { value().line = text; },
        { key: `${prefix}.line`, wide: true, maxLength: 250, placeholder: 'Mahalle, cadde/sokak, no, daire' }),
      input('İlçe', () => value().district, (text) => { value().district = text; },
        { key: `${prefix}.district`, list: 'st-id-districts', maxLength: 60, placeholder: 'Selçuklu' }),
      input('İl', () => value().city, (text) => { value().city = text; },
        { key: `${prefix}.city`, list: 'st-id-cities', maxLength: 60, placeholder: 'Konya' }),
    );
    return grid;
  }

  function suggestButton(text, apply) {
    if (!text || !editable()) return null;
    const chip = h('button', 'st-id-suggest', `← ${text}`);
    chip.type = 'button';
    chip.title = 'Veliden doldur';
    chip.addEventListener('click', () => { apply(text); paint(); paintDirty(); });
    return chip;
  }

  function paint() {
    errorsByKey.clear();
    content.replaceChildren();
    paintSummary();
    const s = form.student;
    const masked = (field) => (view.student?.[`${field}Masked`] && !view.revealed
      ? view.student[`${field}Masked`] : '');
    if (view.cipherError) content.append(h('p', 'st-lc-warn', view.cipherError));
    else if (!can('bbd_students.manage')) {
      content.append(h('p', 'st-hint', 'Görüntüleme yetkiniz var: kimlik numaraları maskeli, düzenleme kapalı.'));
    }

    content.append(h('p', 'st-subhead', 'Öğrenci'));
    const grid = h('div', 'st-grid');
    const sugg = view.suggestions || {};
    grid.append(
      input('TC kimlik no', () => s.nationalId, (v) => { s.nationalId = v; }, {
        key: 'student.nationalId', inputMode: 'numeric', maxLength: 11,
        placeholder: masked('nationalId') || '11 hane', check: M.nationalIdProblem }),
      input('Pasaport / yabancı kimlik no', () => s.foreignId, (v) => { s.foreignId = v; }, {
        key: 'student.foreignId', maxLength: 20, placeholder: masked('foreignId') || 'Yabancı uyruklu ise' }),
      input('Doğum tarihi', () => s.birthDate, (v) => { s.birthDate = v; }, {
        key: 'student.birthDate', placeholder: 'GG.AA.YYYY', inputMode: 'numeric', maxLength: 10,
        check: (v) => (M.isoDay(v) === null ? 'Tarih GG.AA.YYYY biçiminde olmalı.' : null) }),
      input('Cinsiyet', () => s.gender, (v) => { s.gender = v; }, { key: 'student.gender', choices: M.GENDERS }),
      input('Kayıt tarihi', () => s.registeredOn, (v) => { s.registeredOn = v; }, {
        key: 'student.registeredOn', placeholder: 'GG.AA.YYYY', inputMode: 'numeric', maxLength: 10,
        check: (v) => (M.isoDay(v) === null ? 'Tarih GG.AA.YYYY biçiminde olmalı.' : null) }),
      input('Okul adı', () => s.schoolName, (v) => { s.schoolName = v; }, { key: 'student.schoolName', maxLength: 120 }),
      input('Ev telefonu', () => s.homePhone, (v) => { s.homePhone = v; }, {
        key: 'student.homePhone', inputMode: 'tel', placeholder: '0332 xxx xx xx',
        check: (v) => (String(v).trim() && !M.cleanAnyPhone(v) ? 'Ev telefonu 0xxx xxx xx xx biçiminde olmalı.' : null) }),
      input('Anne adı', () => s.motherName, (v) => { s.motherName = v; }, {
        key: 'student.motherName', maxLength: 80,
        extra: !s.motherName ? suggestButton(sugg.motherName, (text) => { s.motherName = text; }) : null }),
      input('Baba adı', () => s.fatherName, (v) => { s.fatherName = v; }, {
        key: 'student.fatherName', maxLength: 80,
        extra: !s.fatherName ? suggestButton(sugg.fatherName, (text) => { s.fatherName = text; }) : null }),
    );
    content.append(grid, addressFields('student.address', () => s.address, 'Adres'));

    for (const g of form.guardians) {
      const title = [`${g.slot}. veli`, g.relationLabel, g.phone ? `0${g.phone.slice(0, 3)} ${g.phone.slice(3, 6)} ${g.phone.slice(6, 8)} ${g.phone.slice(8)}` : '']
        .filter(Boolean).join(' · ');
      content.append(h('p', 'st-subhead', title));
      const shown = (view.guardians || []).find((row) => row.slot === g.slot) || {};
      const gGrid = h('div', 'st-grid');
      gGrid.append(
        input('Ad soyad (yasal, tam)', () => g.fullName, (v) => { g.fullName = v; }, {
          key: `g${g.slot}.fullName`, maxLength: 80, placeholder: 'Nüfus kaydındaki gibi' }),
        input('TC kimlik no', () => g.nationalId, (v) => { g.nationalId = v; }, {
          key: `g${g.slot}.nationalId`, inputMode: 'numeric', maxLength: 11,
          placeholder: (!view.revealed && shown.nationalIdMasked) || '11 hane', check: M.nationalIdProblem }),
        input('Pasaport / yabancı kimlik no', () => g.foreignId, (v) => { g.foreignId = v; }, {
          key: `g${g.slot}.foreignId`, maxLength: 20, placeholder: (!view.revealed && shown.foreignIdMasked) || '' }),
        input('Meslek', () => g.occupation, (v) => { g.occupation = v; }, { key: `g${g.slot}.occupation`, maxLength: 80 }),
        input('İş telefonu', () => g.workPhone, (v) => { g.workPhone = v; }, {
          key: `g${g.slot}.workPhone`, inputMode: 'tel', placeholder: '0332 xxx xx xx',
          check: (v) => (String(v).trim() && !M.cleanAnyPhone(v) ? 'İş telefonu 0xxx xxx xx xx biçiminde olmalı.' : null) }),
        input('E-posta', () => g.email, (v) => { g.email = v; }, {
          key: `g${g.slot}.email`, inputMode: 'email', maxLength: 120, check: M.emailProblem }),
      );
      content.append(gGrid, addressFields(`g${g.slot}.homeAddress`, () => g.homeAddress, 'Ev adresi'));
      if (editable()) {
        const same = button('Öğrencinin adresiyle aynı', { onClick: () => {
          g.homeAddress = { ...s.address };
          paint();
          paintDirty();
        } });
        same.classList.add('st-id-same');
        content.append(same);
      }
      const work = h('details', 'st-id-work');
      if (M.addressComplete(g.workAddress) || g.workAddress.line) work.open = true;
      work.append(h('summary', null, 'İş adresi'), addressFields(`g${g.slot}.workAddress`, () => g.workAddress, 'İş adresi'));
      content.append(work);
    }
    if (!form.guardians.length) {
      content.append(h('p', 'st-hint', 'Veli yok — önce “Veliler” bölümünden veliyi telefonuyla ekleyin; '
        + 'senet ödeme sorumlusu veli adına çıkar.'));
    }
    save.hidden = !editable();
    paintDirty();
  }

  function body() {
    return form && view ? M.diffBody(M.toForm(view), form) : null;
  }

  function paintDirty() {
    const changed = Boolean(body());
    dirtyNote.classList.toggle('show', changed);
    save.disabled = !changed || saving;
  }

  async function submit() {
    if (!editable() || saving) return;
    const found = M.problems(form);
    errors.replaceChildren(...found.map((item) => h('li', null, item.message)));
    for (const item of found) showError(item.key, item.message);
    if (found.length) {
      errorsByKey.get(found[0].key)?.node.focus();
      return;
    }
    const payload = body();
    if (!payload) return;
    saving = true;
    save.disabled = true;
    save.setAttribute('aria-busy', 'true');
    try {
      const result = await api(`${BASE}/students/${enc(kantinId)}/identity`, { method: 'PUT', body: payload });
      if (destroyed) return;
      errors.replaceChildren();
      toast('Kimlik bilgileri kaydedildi — Akademi birkaç saniyede günceller.');
      await reload();
      options.onSaved?.(result);
    } catch (failure) {
      const details = failure?.body?.error?.details || failure?.details || [];
      const lines = Array.isArray(details) && details.length
        ? details.map((item) => item.message) : [`Kaydedilemedi: ${failure?.message || 'bilinmeyen hata'}`];
      errors.replaceChildren(...lines.map((text) => h('li', null, text)));
      for (const item of Array.isArray(details) ? details : []) {
        const key = item.scope === 'guardian' ? `g${item.slot}.${item.field}` : `student.${item.field}`;
        showError(key, item.message);
      }
    } finally {
      saving = false;
      save.removeAttribute('aria-busy');
      paintDirty();
    }
  }

  reload();
  return {
    reload,
    destroy: () => { destroyed = true; },
    dirty: () => Boolean(body()),
  };
}
