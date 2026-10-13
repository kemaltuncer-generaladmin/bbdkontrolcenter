// Öğrenci kaydı — TEK ÖĞRENCİNİN Kontrol Merkezi kaydı (kimlik, sınıf ve
// gruplar, veliler, kantin, durum).
//
// NEREDE ÇİZİLİR. Öğrenci yönetimi 13.10.2026'da BBD Akademi paneline taşındı
// (kullanıcı isteği). Bu bileşen iki yerde aynıdır:
//   · BBD Akademi → Öğrenciler → öğrenci kartı → "Kayıt ve veliler" sekmesi
//     (`bbd_students.record` arayüz yeteneği; K3 — Akademi paneli bu dosyayı
//     import etmez, yetenek olarak çözer),
//   · bu modülün kendi ekranı (Akademi'yi göremeyen roller için; index.js).
//
// VERİ SAHİPLİĞİ DEĞİŞMEDİ: kayıt bu modüldedir, kantin otoritedir. Yazmalar
// bu modülün uçlarından geçer (önce KM, sonra kantin; olaylar outbox'tan):
//
//   GET   /api/bbd_students/students/{id}                 bbd_students.view
//   PATCH /api/bbd_students/students/{id}                 bbd_students.manage
//   GET   /api/bbd_students/students/{id}/guardians       bbd_students.view
//   PUT   /api/bbd_students/students/{id}/guardians       bbd_students.manage
//   GET   /api/bbd_students/students/{id}/groups          bbd_students.view
//   POST  /api/bbd_students/groups/{gid}/members(/end)    bbd_students.groups
//   …/deactivate · /reactivate · DELETE                   bbd_students.lifecycle / .delete
//
// ÇAKIŞMAMA: yalnız DEĞİŞEN alanlar gönderilir (kasa tableti aynı anda başka
// alanı yazıyorsa onunki korunur). Veliler tam liste olarak gider (sıra =
// öncelik, ilk veli birincil).
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { button, confirmSimple, todayIso } from '../../ui-kit/kit.js';
import { formatBalance, normalizeClass, normalizePhone, formatPhone, validate } from './student.js';
import {
  EXAM_CHOICES, GRADE_CHOICES, TRACK_CHOICES, describe, fillFromSuggestion, parseChoice,
  parseGrade, suggestedExam, trackAllowed,
} from './curriculum.js';
import * as G from './guardian-model.js';
import { mountControls } from './canteen.js';
import { askDeactivate, askDelete, mountStatus } from './lifecycle.js';
import { trDay, validOn } from './groups.js';

const BASE = '/api/bbd_students';
const enc = encodeURIComponent;

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// Kaydın kendi alanları (PATCH). Veli alanları "Veliler" bölümündedir.
const FIELDS = ['firstName', 'lastName', 'className', 'schoolNo', 'studentPhone', 'note',
  'gradeLevel', 'track', 'examTarget'];

function card(title, hint) {
  const box = h('section', 'st-card');
  box.append(h('h4', null, title));
  if (hint) box.append(h('p', 'st-hint', hint));
  return box;
}

function loadCss() {
  const href = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${href}"]`)) {
    const link = h('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.append(link);
  }
}

/**
 * @param {HTMLElement} host
 * @param {object} options
 *   api, kantinId, can(izin) → bool, capability(ad) | null, dialogRoot,
 *   toast(metin, ton?), onChanged?(öğrenci), onRemoved?(), classes?() → [sınıf adı]
 * @returns {{reload: () => Promise<void>, destroy: () => void, dirty: () => boolean}}
 */
export function mountRecord(host, options) {
  loadCss();
  const { api, kantinId } = options;
  const can = options.can || (() => true);
  const dialogRoot = options.dialogRoot || host;
  const toast = options.toast || (() => {});
  let destroyed = false;
  let student = null;          // sunucu hâli
  let draft = null;            // düzenlenen hâli
  let guardiansBefore = [];    // sunucunun veli listesi
  let guardians = [];          // düzenlenen veli listesi
  let guardianMeta = { max: G.MAX_GUARDIANS };
  const parts = [];            // destroy edilecek alt bileşenler

  const root = h('div', 'st st-rec');
  host.replaceChildren(root);

  const head = h('header', 'st-rec-head');
  const title = h('h3', 'st-rec-title', 'Öğrenci');
  const subtitle = h('span', 'st-subtitle');
  const badges = h('span', 'st-rec-badges');
  const dirtyNote = h('span', 'st-dirty');
  const save = button('Kaydet', { variant: 'primary', onClick: () => saveAll() });
  save.disabled = true;
  const off = button('Pasifleştir', { variant: 'danger', onClick: () => deactivate() });
  const on = button('Yeniden etkinleştir', { onClick: () => reactivate() });
  const remove = button('Kalıcı sil', { variant: 'danger', onClick: () => removeStudent() });
  const actions = h('div', 'st-rec-actions');
  actions.append(dirtyNote, off, on, remove, save);
  const titleBox = h('div', 'st-rec-name');
  titleBox.append(title, subtitle, badges);
  head.append(titleBox, actions);

  const errors = h('ul', 'st-errors');
  const body = h('div', 'st-rec-body');
  root.append(head, errors, body);
  body.append(h('p', 'st-hint', 'Kayıt okunuyor…'));

  // ------------------------------------------------------------ yükleme

  async function reload() {
    try {
      const [fresh, people] = await Promise.all([
        api(`${BASE}/students/${enc(kantinId)}`),
        api(`${BASE}/students/${enc(kantinId)}/guardians`).catch(() => null),
      ]);
      if (destroyed) return;
      student = fresh;
      draft = { ...fresh };
      guardianMeta = people || { max: G.MAX_GUARDIANS };
      guardiansBefore = G.fromServer(people ? people.items : fresh.guardians);
      guardians = guardiansBefore.map((row) => ({ ...row }));
      render();
    } catch (failure) {
      if (destroyed) return;
      body.replaceChildren(h('p', 'st-status bad', failure?.status === 404
        ? 'Öğrenci kaydı bulunamadı (silinmiş olabilir).'
        : `Kayıt okunamadı: ${failure?.message || 'bilinmeyen hata'}`));
      const retry = button('Tekrar dene', { onClick: () => reload() });
      body.append(retry);
    }
  }

  // -------------------------------------------------------------- çizim

  const inputs = {};

  function field(label, key, opts = {}) {
    const wrap = h('label', `st-field${opts.wide ? ' st-field-wide' : ''}`);
    wrap.append(h('span', 'st-label', label));
    let input;
    if (opts.type === 'textarea') input = h('textarea', 'st-input st-textarea');
    else if (opts.type === 'select') {
      input = h('select', 'st-input st-select');
      for (const [value, text] of opts.choices || []) input.append(new Option(text, value));
    } else {
      input = h('input', 'st-input');
      input.type = 'text';
      if (opts.placeholder) input.placeholder = opts.placeholder;
      if (opts.list) input.setAttribute('list', opts.list);
    }
    if (opts.maxLength) input.maxLength = opts.maxLength;
    input.disabled = !can('bbd_students.manage');
    const value = draft[key];
    input.value = opts.display ? opts.display(value) : (value === null || value === undefined ? '' : String(value));
    input.addEventListener(opts.type === 'select' ? 'change' : 'input', () => {
      draft[key] = opts.parse ? opts.parse(input.value) : input.value;
      opts.after?.();
      paintDirty();
    });
    inputs[key] = input;
    wrap.append(input);
    return wrap;
  }

  function render() {
    for (const part of parts.splice(0)) {
      try { part?.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
    }
    paintHead();
    body.replaceChildren();
    const managed = can('bbd_students.manage');

    if (student.source === 'canteen') {
      body.append(h('p', 'st-lc-warn', 'Kasadan eklendi — sınıf, veli ve seviye bilgilerini tamamlayıp '
        + 'kaydedin. Kaydedince bu not kalkar.'));
    }
    if (student.canteenUnavailable) {
      body.append(h('p', 'st-status bad', 'Kantine şu an ulaşılamıyor — kantin bilgileri son okunan '
        + 'hâlidir; kaydettiğiniz değişiklik kantin dönünce iletilir.'));
    }

    // Durum (km-merkezi-senkron §5)
    const status = card('Durum');
    const statusBox = h('div', 'st-lc');
    status.append(statusBox);
    parts.push(mountStatus(statusBox, { api, student }));

    // Kimlik
    const identity = card('Kimlik', managed ? '' : 'Bu kaydı görüntüleme yetkiniz var; düzenleme '
      + 'yetkiniz yok.');
    const idGrid = h('div', 'st-grid');
    idGrid.append(field('Ad', 'firstName', { maxLength: 60 }), field('Soyad', 'lastName', { maxLength: 60 }),
      field('Okul No', 'schoolNo', { maxLength: 20 }),
      field('Öğrenci telefonu', 'studentPhone', { placeholder: '+90 5xx xxx xx xx', display: formatPhone,
        parse: normalizePhone }));
    identity.append(idGrid, field('Not', 'note', { type: 'textarea', maxLength: 500, wide: true }));

    // Sınıf, seviye ve gruplar
    const school = card('Sınıf ve gruplar', 'Ana sınıf tektir (kantin kartı, Akademi sınıfı ve müfredat '
      + 'önerisi ondan gelir). Öğrenci aynı anda birden çok gruba üye olabilir (etüt, kurs, özel ders).');
    const classGrid = h('div', 'st-grid');
    const listId = `st-classes-${Math.random().toString(36).slice(2, 8)}`;
    const datalist = h('datalist');
    datalist.id = listId;
    for (const name of options.classes?.() || []) datalist.append(new Option(name, name));
    classGrid.append(field('Ana sınıf', 'className', { placeholder: '11-A', maxLength: 20, list: listId,
      parse: (value) => value, after: () => syncCurriculum() }), datalist);
    const display = (value) => (value === null || value === undefined ? '' : String(value));
    classGrid.append(
      field('Seviye', 'gradeLevel', { type: 'select', choices: GRADE_CHOICES, parse: parseGrade, display,
        after: () => syncCurriculum() }),
      field('Alan (10. sınıf ve üstü)', 'track', { type: 'select', choices: TRACK_CHOICES, parse: parseChoice,
        display, after: () => syncCurriculum() }),
      field('Hedef sınav', 'examTarget', { type: 'select', choices: EXAM_CHOICES, parse: parseChoice, display,
        after: () => syncCurriculum() }));
    const suggest = h('div', 'st-suggest');
    school.append(classGrid, suggest);
    const groupsBox = h('div', 'st-rec-groups');
    school.append(h('p', 'st-subhead', 'Gruplar'), groupsBox);
    parts.push(mountGroupsPart(groupsBox));
    school.suggest = suggest;

    // Veliler
    const people = card('Veliler', 'İlk veli birincildir: telefonu kantin kaydına yazılır. En çok '
      + `${guardianMeta.max || G.MAX_GUARDIANS} veli. Aynı telefon kardeşlerin velisi olarak da kayıtlı olabilir.`);
    const peopleBox = h('div', 'st-rec-guardians');
    people.append(peopleBox);

    // Kantin — iki para ayrı (A12): yalnız KANTİN hesabı.
    const canteen = card('Kantin', 'Yalnız kantin hesabıdır; kurum (Akademi) ödemeleriyle karışmaz. '
      + 'Ayarlar kantinde durur ve satış anında orada uygulanır.');
    const controls = h('div', 'st-cc');
    canteen.append(controls);
    parts.push(mountControls(controls, { api, studentId: kantinId, dialogRoot,
      canEdit: can('bbd_students.canteen_controls'), onSaved: () => options.onChanged?.(student) }));
    const pay = h('div', 'st-pay');
    canteen.append(h('p', 'st-subhead', 'Tahsilat ve ödeme talebi'), pay);
    const draw = options.capability?.('bbd_payment_request.student_card');
    if (typeof draw === 'function') {
      parts.push(draw(pay, { studentId: kantinId, displayName: student.displayName, dialogRoot,
        onChanged: () => reload() }));
    } else {
      pay.append(h('p', 'st-hint', 'Kantin tahsilatı ve ödeme talebi “Ödeme Talebi” ekranından yapılır.'));
    }
    canteen.append(accessPart());

    body.append(status, identity, school, people, canteen);
    syncCurriculum(school.suggest);
    paintGuardians(peopleBox);
    paintDirty();
  }

  function paintHead() {
    title.textContent = student.displayName || 'Adsız öğrenci';
    subtitle.textContent = [student.className || 'sınıf yok', `kantin bakiyesi ${formatBalance(student.balance)}`]
      .join(' · ');
    badges.replaceChildren();
    const badge = (text, tone = '') => badges.append(h('i', `st-badge ${tone}`, text));
    if (student.status === 'inactive') badge('pasif', 'st-badge-inactive');
    else if (student.scheduledDeactivation) badge(`${student.scheduledDeactivation} pasif`, 'st-badge-warn');
    if (student.canteenSync === 'pending') {
      badge(student.canteenStale ? 'kantine iletilemedi (24 sa+)' : 'kantine iletilemedi',
        student.canteenStale ? 'st-badge-bad' : 'st-badge-warn');
    }
    if (student.isBlocked) badge('kantin engelli', 'st-badge-warn');
    const inactive = student.status === 'inactive';
    off.hidden = inactive || !can('bbd_students.lifecycle');
    off.textContent = student.scheduledDeactivation ? 'Pasifleştirme planlı' : 'Pasifleştir';
    off.disabled = Boolean(student.scheduledDeactivation);
    on.hidden = !(inactive || student.scheduledDeactivation) || !can('bbd_students.lifecycle');
    on.textContent = inactive ? 'Yeniden etkinleştir' : 'Planı iptal et';
    remove.hidden = !inactive || !can('bbd_students.delete');
    save.hidden = !can('bbd_students.manage');
  }

  function syncCurriculum(slot) {
    const suggest = slot || body.querySelector('.st-suggest');
    if (!draft || !inputs.track) return;
    const allowed = trackAllowed(draft.gradeLevel ?? null);
    inputs.track.disabled = !allowed || !can('bbd_students.manage');
    inputs.track.title = allowed ? '' : 'Alan yalnız 10. sınıf ve üstünde ya da mezunda seçilir.';
    if (!allowed && draft.track) {
      draft.track = null;
      inputs.track.value = '';
    }
    if (!suggest) return;
    suggest.replaceChildren();
    const hint = suggestedExam(draft.gradeLevel ?? null);
    if (hint && !draft.examTarget) suggest.append(h('span', 'st-hint', `Önerilen hedef sınav: ${hint}. `));
    const changes = fillFromSuggestion(draft);
    if (Object.keys(changes).length === 0 || !can('bbd_students.manage')) return;
    suggest.append(h('span', null, `Sınıf adından öneri (${draft.className}): ${describe(draft.curriculumSuggestion)} `));
    suggest.append(button('Öneriyi uygula', { onClick: () => {
      Object.assign(draft, fillFromSuggestion(draft));
      for (const key of ['gradeLevel', 'track', 'examTarget']) {
        inputs[key].value = draft[key] === null || draft[key] === undefined ? '' : String(draft[key]);
      }
      syncCurriculum();
      paintDirty();
    } }));
  }

  // ------------------------------------------------------------ gruplar

  function mountGroupsPart(box) {
    let alive = true;
    const list = h('div', 'st-rec-group-list');
    const tools = h('div', 'st-rec-group-tools');
    box.append(list, tools);
    const editable = can('bbd_students.groups');

    async function load() {
      list.replaceChildren(h('p', 'st-hint', 'Gruplar okunuyor…'));
      try {
        const [mine, all] = await Promise.all([
          api(`${BASE}/students/${enc(kantinId)}/groups`),
          editable ? api(`${BASE}/groups?archived=live`).catch(() => ({ items: [] })) : { items: [] },
        ]);
        if (!alive) return;
        paint(mine.items || [], all.items || all.groups || []);
      } catch (failure) {
        if (alive) list.replaceChildren(h('p', 'st-hint', `Gruplar okunamadı: ${failure.message}`));
      }
    }

    function paint(items, groups) {
      const today = todayIso();
      const current = items.filter((item) => validOn(item, today));
      const past = items.filter((item) => !validOn(item, today));
      list.replaceChildren();
      if (!current.length) list.append(h('p', 'st-hint', 'Öğrenci şu an hiçbir grupta değil.'));
      for (const item of current) {
        const row = h('div', 'st-rec-chip');
        row.append(h('b', null, item.group.name), h('span', 'st-item-meta', `${trDay(item.validFrom)} →`));
        if (editable && !item.group.archived) {
          row.append(button('Çıkar', { onClick: () => leave(item) }));
        }
        list.append(row);
      }
      if (past.length) {
        const old = h('details', 'st-rec-past');
        old.append(h('summary', null, `Geçmiş üyelikler (${past.length})`));
        for (const item of past) {
          old.append(h('div', 'gr-history-row gr-past',
            `${item.group.name} · ${trDay(item.validFrom)} → ${trDay(item.validTo)}`));
        }
        list.append(old);
      }
      tools.replaceChildren();
      if (!editable || student.status === 'inactive') return;
      const inside = new Set(current.map((item) => item.group.id));
      const choices = groups.filter((group) => !inside.has(group.id) && !group.archived);
      if (!choices.length) return;
      const select = h('select', 'st-input st-select');
      select.append(new Option('Gruba ekle…', ''));
      for (const group of choices) select.append(new Option(group.name, group.id));
      const add = button('Ekle', { onClick: () => join(select.value) });
      tools.append(select, add);
    }

    async function join(groupId) {
      if (!groupId) return;
      try {
        await api(`${BASE}/groups/${enc(groupId)}/members`, { method: 'POST',
          body: { studentIds: [kantinId] } });
        toast('Gruba eklendi.');
        load();
      } catch (failure) {
        showErrors([`Gruba eklenemedi: ${failure.message}`]);
      }
    }

    async function leave(item) {
      if (!await confirmSimple(dialogRoot, { title: `“${item.group.name}” grubundan çıkarılsın mı?`,
        description: 'Üyelik bugün biter (geçmiş korunur).', confirmLabel: 'Çıkar' })) return;
      try {
        await api(`${BASE}/groups/${enc(item.group.id)}/members/end`, { method: 'POST',
          body: { studentIds: [kantinId] } });
        toast('Gruptan çıkarıldı.');
        load();
      } catch (failure) {
        showErrors([`Gruptan çıkarılamadı: ${failure.message}`]);
      }
    }

    load();
    return { destroy() { alive = false; }, reload: load };
  }

  // ------------------------------------------------------------ veliler

  function paintGuardians(box) {
    const target = box || body.querySelector('.st-rec-guardians');
    if (!target) return;
    target.replaceChildren();
    const editable = can('bbd_students.manage') && student.status !== 'inactive';
    const problems = G.validate(guardians);
    if (!guardians.length) {
      target.append(h('p', 'st-hint', 'Henüz veli eklenmedi. Velinin uygulamaya girebilmesi ve kantin '
        + 'bilgilendirmeleri için en az bir veli telefonu gerekir.'));
    }
    guardians.forEach((row, index) => {
      const line = h('div', 'st-rec-guardian');
      const rank = h('span', `st-rec-rank${index === 0 ? ' primary' : ''}`, index === 0 ? 'Birincil' : `${index + 1}. veli`);
      const name = h('input', 'st-input');
      name.placeholder = 'Ad soyad';
      name.maxLength = 80;
      name.value = row.name;
      name.disabled = !editable;
      name.addEventListener('input', () => { row.name = name.value; paintDirty(); });
      const relation = h('select', 'st-input st-select');
      for (const [value, text] of G.RELATIONS) relation.append(new Option(text, value));
      relation.value = row.relation || '';
      relation.disabled = !editable;
      relation.addEventListener('change', () => { row.relation = relation.value; paintDirty(); });
      const phone = h('input', 'st-input');
      phone.placeholder = '+90 5xx xxx xx xx';
      phone.inputMode = 'tel';
      phone.maxLength = 24;
      phone.value = G.showPhone(row.phone);
      phone.disabled = !editable;
      phone.addEventListener('input', () => { row.phone = phone.value; paintDirty(false); });
      phone.addEventListener('blur', () => {
        if (G.cleanPhone(phone.value)) phone.value = G.showPhone(phone.value);
        paintGuardians();
      });
      const problem = problems.find((item) => item.index === index);
      if (problem) phone.classList.add('st-invalid');
      line.append(rank, name, relation, phone);
      if (editable) {
        const tools = h('span', 'st-rec-guardian-tools');
        if (index > 0) tools.append(button('Birincil yap', { onClick: () => {
          guardians = G.makePrimary(guardians, index); paintGuardians(); paintDirty();
        } }));
        tools.append(button('Sil', { variant: 'danger', onClick: () => {
          guardians = G.remove(guardians, index); paintGuardians(); paintDirty();
        } }));
        line.append(tools);
      }
      target.append(line);
      const info = [];
      if (index >= 2) info.push('Uygulama girişi şimdilik ilk iki veli için açılır; bu veli Kontrol Merkezi kaydında durur.');
      const siblings = (row.siblings || []).filter(Boolean);
      if (siblings.length && G.cleanPhone(row.phone)) {
        info.push(`Bu telefon ${siblings.map((item) => `${item.displayName}${item.className ? ` (${item.className})` : ''}`)
          .join(', ')} için de veli.`);
      }
      if (problem) info.push(problem.message);
      if (info.length) target.append(h('p', `st-hint st-rec-guardian-note${problem ? ' bad' : ''}`, info.join(' ')));
    });
    const note = G.primaryNote(guardiansBefore, guardians);
    if (note) target.append(h('p', 'st-lc-warn', note));
    if (editable && guardians.length < (guardianMeta.max || G.MAX_GUARDIANS)) {
      target.append(button('+ Veli ekle', { onClick: () => { guardians = G.add(guardians); paintGuardians(); paintDirty(); } }));
    }
  }

  // ------------------------------------------------------------- kayıt

  function dirtyFields() {
    if (!student || !draft) return [];
    return FIELDS.filter((key) => String(student[key] ?? '') !== String(draft[key] ?? ''));
  }

  function guardiansDirty() {
    return G.changed(guardiansBefore, guardians);
  }

  function paintDirty(withErrors = true) {
    const count = dirtyFields().length + (guardiansDirty() ? 1 : 0);
    save.disabled = count === 0;
    dirtyNote.textContent = count === 0 ? '' : 'Kaydedilmemiş değişiklik var';
    dirtyNote.classList.toggle('show', count > 0);
    if (withErrors && draft) {
      showErrors([...validate({ ...draft, parentPhone: '', parentPhone2: '' }),
        ...(guardiansDirty() ? G.validate(guardians).map((item) => item.message) : [])]);
    }
  }

  function showErrors(list) {
    errors.replaceChildren(...list.map((text) => h('li', null, text)));
    errors.classList.toggle('show', list.length > 0);
  }

  async function saveAll() {
    const fields = dirtyFields();
    const people = guardiansDirty();
    const problems = [...validate({ ...draft, parentPhone: '', parentPhone2: '' }),
      ...(people ? G.validate(guardians).map((item) => item.message) : [])];
    if (problems.length) { showErrors(problems); return; }
    if (people && G.primaryNote(guardiansBefore, guardians) && !await confirmSimple(dialogRoot, {
      title: 'Birincil veli değişiyor', description: G.primaryNote(guardiansBefore, guardians),
      confirmLabel: 'Kaydet' })) return;
    save.disabled = true;
    save.textContent = 'Kaydediliyor…';
    let pending = false;
    try {
      if (fields.length) {
        const payload = {};
        for (const key of fields) {
          if (key === 'studentPhone') payload[key] = normalizePhone(draft[key]);
          else if (key === 'className') payload[key] = normalizeClass(draft[key]);
          else payload[key] = draft[key];
        }
        if (fields.includes('firstName') || fields.includes('lastName')) {
          payload.firstName = draft.firstName;
          payload.lastName = draft.lastName;
        }
        const result = await api(`${BASE}/students/${enc(kantinId)}`, { method: 'PATCH', body: payload });
        pending = pending || result?.canteenSync === 'pending';
      }
      if (people) {
        const result = await api(`${BASE}/students/${enc(kantinId)}/guardians`, { method: 'PUT',
          body: G.toPayload(guardians) });
        pending = pending || result?.canteenSync === 'pending';
      }
      toast(pending ? 'Kaydedildi — kantine iletilemedi, otomatik yeniden denenecek.' : 'Kaydedildi.');
      await reload();
      options.onChanged?.(student);
    } catch (failure) {
      showErrors([`Kaydedilemedi: ${failure.message}`]);
    } finally {
      save.textContent = 'Kaydet';
      paintDirty(false);
    }
  }

  // -------------------------------------------------- yaşam döngüsü (§5)

  async function lifecycle(path, init, done) {
    try {
      const result = await api(path, init);
      await reload();
      options.onChanged?.(student);
      done?.(result);
    } catch (failure) {
      showErrors([failure.message]);
    }
  }

  async function deactivate() {
    const answer = await askDeactivate(dialogRoot, { students: [student] });
    if (!answer) return;
    await lifecycle(`${BASE}/students/${enc(kantinId)}/deactivate`, { method: 'POST', body: answer },
      (result) => toast(result?.scheduledFor ? `Pasifleştirme ${result.scheduledFor} için planlandı.`
        : result?.canteenSync === 'pending' ? 'Pasifleştirildi — kantine iletilemedi, otomatik yeniden denenecek.'
          : 'Pasifleştirildi.'));
  }

  async function reactivate() {
    const scheduled = student.status !== 'inactive';
    if (!await confirmSimple(dialogRoot, {
      title: scheduled ? 'Planlı pasifleştirme iptal edilsin mi?' : 'Yeniden etkinleştir',
      description: scheduled ? 'Öğrenci etkin kalır.'
        : 'Kantin satışı ve BBD Akademi girişi yeniden açılır. Grup üyelikleri kendiliğinden açılmaz — '
          + 'gerekiyorsa “Sınıf ve gruplar” bölümünden ekleyin.',
      confirmLabel: scheduled ? 'Planı iptal et' : 'Yeniden etkinleştir',
    })) return;
    await lifecycle(`${BASE}/students/${enc(kantinId)}/reactivate`, { method: 'POST' },
      (result) => toast(result?.scheduleCancelled ? 'Plan iptal edildi.'
        : 'Yeniden etkinleştirildi — grup üyeliklerini kontrol edin.'));
  }

  async function removeStudent() {
    const confirmName = await askDelete(dialogRoot, { student });
    if (!confirmName) return;
    try {
      await api(`${BASE}/students/${enc(kantinId)}`, { method: 'DELETE', body: { confirmName } });
      toast('Öğrenci kalıcı silindi.');
      options.onRemoved?.();
    } catch (failure) {
      showErrors([`Silinemedi: ${failure.message}`]);
    }
  }

  // ------------------------------------------------- kart QR ve giriş kodu

  function accessPart() {
    const box = h('details', 'st-rec-access');
    box.append(h('summary', null, 'Kantin kartı QR’ı ve giriş kodu'));
    const out = h('div', 'st-qr-out');
    const code = h('code', 'st-qr-text');
    const copy = button('Kopyala', { onClick: () => navigator.clipboard?.writeText(code.textContent) });
    out.append(code, copy);
    out.hidden = true;
    const qr = button('QR üret', { onClick: async () => {
      out.hidden = true;
      try {
        const result = await api(`${BASE}/students/${enc(kantinId)}/qr`);
        code.textContent = result.qrText;
        out.hidden = false;
      } catch (failure) {
        showErrors([`QR üretilemedi: ${failure.message}`]);
      }
    } });
    const reset = button('Yeni giriş kodu üret', { onClick: async () => {
      if (!await confirmSimple(dialogRoot, { title: 'Yeni giriş kodu üret',
        description: `${student.displayName || 'Öğrenci'} için yeni bir giriş kodu üretilecek. Şu anki kod geçersiz olur.`,
        confirmLabel: 'Yeni kod üret', danger: true })) return;
      out.hidden = true;
      try {
        const result = await api(`${BASE}/students/${enc(kantinId)}/access-code`, { method: 'POST' });
        code.textContent = result.accessCode;     // yalnız üretildiği an gösterilir
        out.hidden = false;
        toast('Yeni giriş kodu üretildi.');
      } catch (failure) {
        showErrors([`Kod sıfırlanamadı: ${failure.message}`]);
      }
    } });
    qr.hidden = !can('bbd_students.qr');
    reset.hidden = !can('bbd_students.access_code');
    const row = h('div', 'st-rec-group-tools');
    row.append(qr, reset);
    box.append(h('p', 'st-hint', 'QR kasadaki anahtarla sunucuda üretilir. Giriş kodunu kantin üretir; '
      + 'sıfırlanınca eski kod geçersiz olur.'), row, out);
    return box;
  }

  reload();
  return {
    reload,
    dirty: () => dirtyFields().length > 0 || guardiansDirty(),
    destroy() {
      destroyed = true;
      for (const part of parts.splice(0)) {
        try { part?.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
      host.replaceChildren();
    },
  };
}
