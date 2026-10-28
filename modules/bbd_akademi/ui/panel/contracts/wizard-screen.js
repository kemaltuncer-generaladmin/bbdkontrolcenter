// BBD Akademi — "Kayıt ve ödeme planı" sihirbazı (F-SZ1, K6; iş 80-km-sihirbaz). ANA KUMANDA KM.
//
// TEK EKRAN, TEK ONAY (kullanıcı 1, 6, 7): öğrenci + veli (A4, maskeli; eksikler işaretli → "Öğrenciler panelinde
// düzenle") → HİZMETLER (katalog A19; satır toplamı canlı) → ÖDEME (peşin/taksit, peşinat, eşit böl, satır satır
// tarih/tutar/TÜR, toplam farkı) → GEÇMİŞ ÖDEMELER (mevcut öğrenci) → SÖZLEŞME (indirim nedenleri, MEB özel
// hususlar, "Basılı imzalı sözleşmesi var" + eski no + imza tarihi) → Önizle (A5 `?pdf=registration`) · Onayla (A6,
// Idempotency-Key) · imzasızsa "Kaydet ve yazdır" (A6 → A17 kayıt seti TEK PDF → KM yazdırma zinciri).
//
// HIZ (dershane sahibi gözü — eski sözleşmeyi klavyeyle < 1 dakikada girip onaylamak): bölümler tek pencerede
// alt alta; Enter bir sonraki alana geçer, Ctrl/⌘+Enter onaylar; akıllı varsayılanlar `wizard-logic.js`'te
// (ilk vade = gelecek ayın sözleşme günü, taksit türü NAKİT, eğitim yılı önerisi, katalogdaki ilk hizmet). Taksitler
// yazarken canlı bölünür; bir satır elle düzeltilince bölme durur, "Eşit böl" geri açar.
//
// SUNUCU ESASTIR: tutar ve toplam kuralı Akademi'dedir; hata zarfları alan alan bölümlerin altına yazılır
// (`contract_totals_mismatch`, `contract_missing_fields`, `contract_number_taken`, `validation_error`).
//
// ANLIK: başarılı onaydan sonra ortak yerel veri yolu (`emitContractEvent` → `bbd-akademi:contract-changed`; 80-km-belge
// ekranı ve öğrencinin Sözleşmeler sekmesi dinler) yayınlanır. Açıkken KM'de kimlik değişirse (`bbd-students:updated`, `contract.party_changed`) taraf yeniden
// okunur — Akademi aynası 1–3 sn'de uygular.

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge } from '../../../ui-kit/layout.js';
import { dateField } from '../../../ui-kit/datefield.js';
import { AkademiError, newKey, problem } from '../api.js';
import { confirmAction, fmtDate, moneyText as moneyField, parseMoney, todayIst } from '../common.js';
import {
  DISCOUNT_REASONS, INSTALLMENT_METHODS, INTERVALS, MEB_FEE_CATEGORIES, PAST_PAYMENT_METHODS, RELATIONS,
  academicYearOptions, addressText, contractProblem, lineFromService, lineTotals, linesSummary, missingFor, missingText,
  nextNumberHint,
} from './shared-contract-logic.js';
import {
  KM_PDF_KINDS, buildDocuments, contractSettings, editInStudentsPanel, emitContractEvent, onContractEvent,
  openContractFile, paths,
} from './shared-contract.js';
import * as W from './wizard-logic.js';

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
const itemsOf = (data) => (Array.isArray(data) ? data : (data?.items || []));

/** Yalnız bir sihirbaz açık olur (ikinci istek açık olanı öne getirir). */
let current = null;

/**
 * @param {object} app  panel bağlamı
 * @param {object} spec
 *   studentId  Akademi öğrenci UUID'si (yoksa `kmRef` ile aranır — yeni öğrenci aynalanırken)
 *   kmRef      KM öğrenci numarası (`STU-…`, Akademi `externalRef`)
 *   planId     "Sözleşme oluştur": bağlanacak mevcut plan
 *   source     'new' | 'existing' | 'missing' | 'plan'
 *   name       başlıkta gösterilecek ad (yüklenene dek)
 * @returns {Promise<object|null>} oluşturulan sözleşme (`ContractDetail`) ya da vazgeçildiyse null
 */
export function openWizardScreen(app, { studentId = null, kmRef = null, planId = null, source = 'existing',
  name = '' } = {}) {
  if (current) {
    current.focus();
    if (current.studentId && studentId && current.studentId !== studentId) {
      app.toast('Açık bir “Kayıt ve ödeme planı” penceresi var; önce onu bitirin ya da kapatın.', 'warn');
    }
    return current.promise;
  }
  loadStyles(import.meta.url, 'wizard-screen.css');
  let resolveOuter;
  const promise = new Promise((resolve) => { resolveOuter = resolve; });
  const screen = buildScreen(app, { studentId, kmRef, planId, source: planId ? 'plan' : source, name },
    (result) => { current = null; resolveOuter(result); });
  current = { promise, focus: screen.focus, studentId };
  return promise;
}

function buildScreen(app, spec, done) {
  const today = todayIst();
  const sessionKey = newKey('km-ctr');
  const cleanup = [];
  let studentId = spec.studentId;
  let kmRef = spec.kmRef;
  let studentName = spec.name;
  let partyResp = null;
  let services = [];
  let settings = null;
  let plan = null;
  let existing = [];
  let state = null;
  let initialPrint = '';
  let showErrors = false;
  let serverFields = [];
  let busy = false;
  let closed = false;

  // ------------------------------------------------------------ iskelet
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog ak-dialog ak-wz');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  const head = h('div', 'ak-wz-head');
  const headText = h('div', 'ak-wz-head-text');
  const title = h('h3', 'kit-dialog-title', `Kayıt ve ödeme planı${studentName ? ` — ${studentName}` : ''}`);
  const subtitle = h('div', 'ak-wz-sub', W.SOURCES[spec.source] || '');
  headText.append(title, subtitle);
  head.append(headText);
  const body = h('div', 'ak-wz-body');
  const foot = h('div', 'ak-wz-foot');
  card.append(head, body, foot);
  overlay.append(card);
  app.root.append(overlay);
  const release = dialogA11y(card);

  const sumLine = h('div', 'ak-wz-sum');
  const errorLine = h('div', 'ak-wz-error');
  errorLine.setAttribute('role', 'alert');
  const cancelBtn = button('Vazgeç', { onClick: () => attemptClose() });
  const previewBtn = button('Önizle', { title: 'Kayıt sözleşmesinin PDF önizlemesi (kayıt yazılmaz).',
    onClick: () => preview() });
  const confirmBtn = button('Onayla', { onClick: () => submit({ print: false }) });
  const printBtn = button('Kaydet ve yazdır', { onClick: () => submit({ print: true }) });
  printBtn.dataset.command = 'print';
  const keys = h('span', 'ak-wz-keys', 'Enter: sonraki alan · Ctrl+Enter: onayla · Esc: kapat');
  const footRow = h('div', 'ak-wz-foot-row');
  footRow.append(keys, h('span', 'kit-spacer'), cancelBtn, previewBtn, confirmBtn, printBtn);
  foot.append(sumLine, errorLine, footRow);
  foot.hidden = true;

  // ------------------------------------------------------------ klavye
  const onKey = (event) => {
    if (closed) return;
    if (event.key === 'Escape') {
      if (event.target?.closest?.('.kit-datefield')) return;      // takvim kendi Esc'ini kullanır
      const layers = document.querySelectorAll('.kit-overlay');
      if (layers[layers.length - 1] !== overlay) return;           // üstte başka pencere: Esc önce onu kapatır
      event.preventDefault();
      attemptClose();
      return;
    }
    if (event.key !== 'Enter' || !card.contains(event.target)) return;
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      if (state) submit({ print: !state.existingSignedOn });
      return;
    }
    const target = event.target;
    if (event.shiftKey || event.altKey || !(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) return;
    event.preventDefault();
    focusNext(target);
  };
  document.addEventListener('keydown', onKey);
  cleanup.push(() => document.removeEventListener('keydown', onKey));

  function fields() {
    return [...body.querySelectorAll('input, select, textarea')]
      .filter((node) => !node.disabled && node.type !== 'hidden' && node.tabIndex !== -1 && node.offsetParent !== null);
  }
  // Enter taksit / geçmiş ödeme SATIRLARINA girmez (düzeltme içindir; Tab ile girilir) — içindeyken satırda ilerler.
  function focusNext(from) {
    const all = fields();
    const index = all.indexOf(from);
    const zone = from.closest('[data-enter-skip]');
    const next = all.slice(index + 1).find((node) => {
      const inside = node.closest('[data-enter-skip]');
      return !inside || inside === zone;
    });
    if (next) {
      next.focus();
      if (next.select && next instanceof HTMLInputElement && next.type === 'text') next.select();
    } else {
      (state?.existingSignedOn ? confirmBtn : printBtn).focus();
    }
  }

  // ------------------------------------------------------------ kapanış
  function close(result) {
    if (closed) return;
    closed = true;
    for (const fn of cleanup.splice(0)) {
      try { fn(); } catch { /* temizlik yarıda kalmaz */ }
    }
    overlay.remove();
    release();
    done(result);
  }
  async function attemptClose() {
    if (busy) return;
    if (state && W.fingerprint(state) !== initialPrint) {
      const ok = await confirmAction(app, { title: 'Girilenler kaydedilmedi',
        description: 'Pencere kapanınca bu sözleşme için girdikleriniz silinir. Öğrenci “Sözleşmesi olmayanlar” listesinde kalır.',
        confirmLabel: 'Kapat', danger: true });
      if (!ok) return;
    }
    close(null);
  }
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) attemptClose(); });

  // ------------------------------------------------------------ yükleme
  const wait0 = waiting('Bilgiler okunuyor…');
  body.replaceChildren(wait0);
  load();

  function waiting(text) {
    const node = h('div', 'ak-wz-wait');
    node.setAttribute('role', 'status');
    const bar = h('div', 'ak-wz-wait-bar');
    bar.append(h('span'));
    node.append(h('div', '', text), bar);
    return node;
  }

  function failed(message, retry) {
    const node = h('div', 'ak-wz-wait');
    node.append(alertBox(message, 'bad'));
    const row = h('div', 'ak-wz-tools');
    if (retry) row.append(button('Tekrar dene', { variant: 'primary', onClick: () => retry() }));
    row.append(button('Kapat', { onClick: () => close(null) }));
    node.append(row);
    body.replaceChildren(node);
  }

  /** Yeni öğrenci: KM'de kaydedildi, Akademi aynası birkaç saniyede gelir — `externalRef` ile aranır. */
  async function resolveStudent() {
    if (studentId) return studentId;
    if (!kmRef) throw new AkademiError({ code: 'student_unknown', message: 'Öğrenci belirlenemedi.' });
    body.replaceChildren(waiting('Öğrenci BBD Akademi\'ye aktarılıyor… (birkaç saniye)'));
    const deadline = Date.now() + 30_000;
    let delay = 900;
    while (!closed && Date.now() < deadline) {
      try {
        const data = await app.api.get('/v1/admin/students', { externalRef: kmRef, limit: 5 }); // eslint-disable-line no-await-in-loop
        const found = itemsOf(data).find((row) => String(row.externalRef || '').toUpperCase() === kmRef);
        if (found) {
          studentName = studentName || found.displayName || '';
          return found.id;
        }
      } catch (failure) {
        if (!['not_found', 'akademi_unavailable'].includes(failure?.code)) throw failure;
      }
      await wait(delay); // eslint-disable-line no-await-in-loop
      delay = Math.min(delay * 1.3, 2500);
    }
    throw new AkademiError({ code: 'mirror_timeout',
      message: 'Öğrenci henüz BBD Akademi\'ye ulaşmadı. Birkaç saniye sonra “Tekrar dene”; vazgeçerseniz öğrenci '
        + '“Sözleşmesi olmayanlar” listesinde kalır, sözleşmeyi oradan çıkarırsınız.' });
  }

  /** A4 — aynalanma yeni bittiyse kısa süre 404 dönebilir: birkaç kez dener. */
  async function readParty() {
    let lastError = null;
    for (let attempt = 0; attempt < 5 && !closed; attempt += 1) {
      try {
        return await app.api.get(paths.party(studentId)); // eslint-disable-line no-await-in-loop
      } catch (failure) {
        lastError = failure;
        if (failure?.status !== 404 && failure?.code !== 'not_found') throw failure;
        await wait(1200 + attempt * 600); // eslint-disable-line no-await-in-loop
      }
    }
    throw lastError || new AkademiError({ message: 'Öğrenci bilgileri okunamadı.' });
  }

  async function load() {
    try {
      studentId = await resolveStudent();
      if (closed) return;
      if (!body.contains(wait0)) body.replaceChildren(waiting('Bilgiler okunuyor…'));
      const canFinance = app.api.can('admin_getFinanceAccount');
      const [party, detail, catalog, conf, contracts, account] = await Promise.all([
        readParty(),
        app.api.get(`/v1/admin/students/${studentId}`).catch(() => null),
        app.api.getCached('/v1/admin/contracts/services', undefined, { ttlMs: 5 * 60_000 }).catch(() => null),
        contractSettings(app).catch(() => null),
        app.api.get(paths.studentContracts(studentId)).catch(() => null),
        spec.planId && canFinance ? app.api.get(`/v1/admin/finance/accounts/${studentId}`).catch(() => null)
          : Promise.resolve(null),
      ]);
      if (closed) return;
      partyResp = party || {};
      const student = detail?.student || {};
      kmRef = kmRef || student.externalRef || null;
      studentName = partyResp.party?.student?.fullName || student.displayName || studentName;
      services = itemsOf(catalog).filter((item) => item && item.active !== false);
      settings = conf;
      existing = W.activeContracts(contracts);
      plan = spec.planId ? (account?.plans || []).find((item) => item.id === spec.planId) || null : null;
      if (spec.planId && !plan) {
        failed('Bağlanacak taksit planı okunamadı (iptal edilmiş ya da finans izni yok). Planı Kurum finansı → cari '
          + 'hesaptan kontrol edin.', () => { body.replaceChildren(waiting('Bilgiler okunuyor…')); load(); });
        return;
      }
      state = W.initialState({ source: spec.source, today, party: partyResp, settings: settings || undefined, plan,
        services });
      initialPrint = W.fingerprint(state);
      title.textContent = `Kayıt ve ödeme planı — ${studentName || 'Öğrenci'}`;
      render();
      watch();
    } catch (failure) {
      if (closed) return;
      failed(problem(failure, 'Bilgiler okunamadı.'), () => {
        body.replaceChildren(waiting('Bilgiler okunuyor…'));
        load();
      });
    }
  }

  /** KM'de kimlik düzenlenince taraf yeniden okunur (Akademi aynası 1–3 sn). */
  function watch() {
    let timers = [];
    const later = () => {
      timers.forEach(clearTimeout);
      timers = [setTimeout(() => reloadParty(), 1800), setTimeout(() => reloadParty(), 4500)];
    };
    const onStudents = () => later();
    window.addEventListener('bbd-students:updated', onStudents);
    const off = onContractEvent((event) => {
      if (event.type !== 'contract.party_changed') return;
      if (event.studentId && event.studentId !== studentId) return;
      if (event.kmId && kmRef && String(event.kmId).toUpperCase() !== String(kmRef).toUpperCase()) return;
      later();
    });
    cleanup.push(() => {
      timers.forEach(clearTimeout);
      window.removeEventListener('bbd-students:updated', onStudents);
      off();
    });
  }

  async function reloadParty() {
    if (closed || !studentId) return;
    try {
      const next = await app.api.get(paths.party(studentId));
      if (closed) return;
      partyResp = next || {};
      const slots = (partyResp.party?.guardians || []).map((item) => item.slot);
      if (!slots.includes(state.payerGuardianSlot)) state.payerGuardianSlot = W.payerSlotOf(partyResp);
      sections.party.paint();
      refresh();
    } catch { /* sessiz: eski bilgi kalır */ }
  }

  // ------------------------------------------------------------ bölümler
  const sections = {};

  function section(key, no, label, hint = '') {
    const node = h('section', 'ak-wz-sec');
    node.dataset.section = key;
    const top = h('div', 'ak-wz-sec-head');
    top.append(h('span', 'ak-wz-sec-no', String(no)), h('span', 'ak-wz-sec-title', label));
    const hintNode = h('span', 'ak-wz-sec-hint', hint);
    top.append(hintNode);
    const content = h('div', 'ak-stack');
    const errors = h('div', 'ak-wz-sec-errors');
    node.append(top, content, errors);
    return { node, content, errors, top, hint: hintNode, destroyers: [] };
  }

  function render() {
    const notices = h('div', 'ak-stack');
    if (existing.length) {
      notices.append(alertBox(`Bu öğrencinin geçerli sözleşmesi var: ${existing.map((item) => `${item.number || '—'}`
        + `${item.academicYear ? ` (${item.academicYear})` : ''}`).join(', ')}. Aynı eğitim yılına ikinci sözleşme açılamaz; `
        + 'başka yıl için yeni sözleşme ayrı bir taksit planı açar.', 'warn'));
    }
    if (plan) {
      notices.append(alertBox(`“${plan.title}” planı bu sözleşmeye bağlanır: ödeme bölümü plandan okunur, yeni plan `
        + 'kurulmaz.', 'info'));
    }
    sections.party = partySection();
    sections.lines = linesSection();
    sections.payment = paymentSection();
    sections.past = state.source === 'new' || plan ? null : pastSection();
    sections.contract = contractSection();
    const numbered = [sections.party, sections.lines, sections.payment, sections.past, sections.contract].filter(Boolean);
    numbered.forEach((item, index) => { item.no.textContent = String(index + 1); });
    body.replaceChildren(...[notices.childElementCount ? notices : null, ...numbered.map((item) => item.node)]
      .filter(Boolean));
    foot.hidden = false;
    refresh();
    // İlk alan: mevcut öğrencide (eski sözleşme aktarımı) sözleşme tarihi, yeni öğrencide ilk hizmetin birim fiyatı.
    const first = state.source === 'new' ? sections.lines.node.querySelector('[data-field="unitPriceMinor"]')
      : sections.party.node.querySelector('[data-field="contractDate"] input');
    (first || fields()[0])?.focus();
    first?.select?.();
  }

  function withNo(made) {
    made.no = made.top.querySelector('.ak-wz-sec-no');
    return made;
  }

  function field(label, control, { key = '' } = {}) {
    const wrap = h('label', 'ak-wz-field');
    if (key) wrap.dataset.field = key;
    wrap.append(h('span', '', label), control instanceof Node ? control : control.node);
    return wrap;
  }

  function textInput(value, onInput, { placeholder = '', max = 120, key = '', mode = 'text', aria = '' } = {}) {
    const input = h('input', 'kit-input');
    input.type = 'text';
    input.value = value ?? '';
    input.placeholder = placeholder;
    input.maxLength = max;
    if (mode !== 'text') input.inputMode = mode;
    if (key) input.dataset.field = key;
    if (aria) input.setAttribute('aria-label', aria);
    input.addEventListener('input', () => onInput(input.value, input));
    return input;
  }

  /** Para alanı: yazarken kuruşa çevrilir (geçersizse kırmızı, değer 0 sayılmaz). */
  function moneyInput(kurus, onValue, { key = '', aria = '', allowEmpty = true } = {}) {
    const input = textInput(kurus === null || kurus === undefined ? '' : moneyField(kurus), (raw) => {
      const parsed = parseMoney(raw);
      const bad = raw.trim() !== '' && parsed === null;
      input.classList.toggle('bad', bad);
      if (!bad) onValue(parsed === null ? (allowEmpty ? null : 0) : parsed);
    }, { key, mode: 'decimal', max: 18, aria });
    input.classList.add('ak-wz-num');
    input.addEventListener('blur', () => {
      const parsed = parseMoney(input.value);
      if (parsed !== null) input.value = moneyField(parsed);
    });
    return input;
  }

  function selectOf(options, value, onChange, { aria = '', key = '' } = {}) {
    const select = h('select', 'kit-select');
    for (const [optionValue, label] of options) {
      const option = h('option', '', label);
      option.value = optionValue;
      select.append(option);
    }
    select.value = value ?? '';
    if (aria) select.setAttribute('aria-label', aria);
    if (key) select.dataset.field = key;
    select.addEventListener('change', () => onChange(select.value));
    return select;
  }

  function dateOf(value, onChange, label, made) {
    const control = dateField({ value: value || '', label, onChange });
    made.destroyers.push(() => control.destroy());
    return control;
  }

  function removeButton(label, onClick) {
    const node = button('×', { variant: 'ghost', title: label, onClick });
    node.classList.add('ak-wz-x');
    node.setAttribute('aria-label', label);
    node.tabIndex = -1;
    return node;
  }

  // ---- 1. Öğrenci ve veli (+ sözleşme tarihleri) -----------------------------------------
  function partySection() {
    const made = withNo(section('party', 1, 'Öğrenci ve veli',
      'Kimlik bilgileri KM Öğrenciler panelinden gelir (maskeli).'));
    const info = h('div');
    const dates = h('div', 'ak-wz-grid');
    made.content.append(info, dates);

    const years = academicYearOptions(state.academicYear);
    const yearSelect = selectOf(years.map((year) => [year, year.replace('-', '–')]), state.academicYear, (value) => {
      state.academicYear = value;
      state.yearLinked = false;
      refresh();
    }, { aria: 'Eğitim yılı', key: 'academicYear' });
    const contractDate = dateOf(state.contractDate, (iso) => {
      if (iso === state.contractDate) return;
      W.setContractDate(state, iso, { startMonth: settings?.academicYearStartMonth || 9 });
      registered.set(state.registeredOn);
      if (state.yearLinked) {
        const options = academicYearOptions(state.academicYear);
        yearSelect.replaceChildren(...options.map((year) => {
          const option = h('option', '', year.replace('-', '–'));
          option.value = year;
          return option;
        }));
        yearSelect.value = state.academicYear;
      }
      sections.payment.paint();
      sections.contract.syncDates();
      refresh();
    }, 'Sözleşme tarihi', made);
    const registered = dateOf(state.registeredOn, (iso) => {
      if (iso === state.registeredOn) return;
      state.registeredOn = iso;
      state.registeredLinked = false;
      refresh();
    }, 'Kayıt tarihi', made);
    dates.append(field('Eğitim yılı', yearSelect), field('Sözleşme tarihi', contractDate, { key: 'contractDate' }),
      field('Kayıt tarihi', registered, { key: 'registeredOn' }));

    made.paint = () => {
      const party = partyResp?.party || {};
      const student = party.student || {};
      const guardians = [...(party.guardians || [])].sort((a, b) => a.slot - b.slot);
      const cols = h('div', 'ak-wz-cols');
      const left = h('div', 'ak-stack');
      const facts = h('dl', 'ak-kv');
      for (const [label, value] of [
        ['Öğrenci', student.fullName || studentName],
        ['Öğrenci no / sınıf', [student.studentNo || kmRef, student.classGroup].filter(Boolean).join(' · ')],
        ['TC kimlik no', student.nationalIdMasked || student.foreignIdMasked],
        ['Doğum tarihi', student.birthDate ? fmtDate(student.birthDate) : ''],
        ['Adres', addressText(student.address)],
      ]) {
        facts.append(h('dt', '', label), h('dd', '', value || '—'));
      }
      left.append(facts);

      const right = h('div', 'ak-stack');
      right.append(h('div', 'ak-wz-sec-hint', 'Ödeme sorumlusu (senetlerin borçlusu)'));
      const list = h('div', 'ak-wz-payers');
      list.setAttribute('role', 'radiogroup');
      list.setAttribute('aria-label', 'Ödeme sorumlusu veli');
      if (!guardians.length) list.append(alertBox('Veli kaydı yok. Öğrenciler panelinde en az bir veli ekleyin.', 'bad'));
      for (const guardian of guardians) {
        const row = h('label', `ak-wz-payer${guardian.slot === state.payerGuardianSlot ? ' on' : ''}`);
        const radio = h('input');
        radio.type = 'radio';
        radio.name = `ak-wz-payer-${sessionKey}`;
        radio.value = String(guardian.slot);
        radio.checked = guardian.slot === state.payerGuardianSlot;
        radio.addEventListener('change', () => {
          state.payerGuardianSlot = guardian.slot;
          list.querySelectorAll('.ak-wz-payer').forEach((node) => node.classList.toggle('on', node === row));
          paintMissing();
          refresh();
        });
        const text = h('div');
        text.append(h('b', '', guardian.fullName || `${guardian.slot}. veli`),
          h('small', '', [RELATIONS[guardian.relation] || (guardian.relation ? guardian.relation : `${guardian.slot}. veli`),
            guardian.nationalIdMasked || guardian.foreignIdMasked ? `TC ${guardian.nationalIdMasked || guardian.foreignIdMasked}` : '',
            guardian.phoneMasked || '', addressText(guardian.homeAddress)].filter(Boolean).join(' · ')));
        row.append(radio, text);
        list.append(row);
      }
      right.append(list);
      cols.append(left, right);

      const missingSlot = h('div', 'ak-wz-missing');
      function paintMissing() {
        const missing = missingFor(partyResp, state.payerGuardianSlot);
        missingSlot.replaceChildren();
        if (!missing.length) {
          missingSlot.append(badge('Belge bilgileri tam', 'good'));
          return;
        }
        missingSlot.append(h('span', 'ak-wz-sec-hint', 'Eksik (belge basılamaz):'));
        for (const item of missing) missingSlot.append(badge(missingText(item), 'bad'));
        const edit = button('Öğrenciler panelinde düzenle', { variant: 'ghost', onClick: () => {
          editInStudentsPanel(app, { kmId: kmRef, name: studentName, onClosed: () => {
            setTimeout(() => reloadParty(), 1500);
            setTimeout(() => reloadParty(), 4000);
          } });
        } });
        edit.tabIndex = -1;
        missingSlot.append(edit);
      }
      paintMissing();
      info.replaceChildren(cols, missingSlot);
    };
    made.paint();
    return made;
  }

  // ---- 2. Hizmetler -----------------------------------------------------------------------
  function linesSection() {
    const made = withNo(section('lines', 2, 'Hizmetler',
      services.length ? 'Katalogdan seçin; fiyat ve indirimler satırda düzenlenir.'
        : 'Hizmet kataloğu boş — satırı elle yazın (Sözleşmeler → Hizmet kataloğu).'));
    const table = h('div', 'ak-wz-table ak-wz-lines');
    const totals = h('div', 'ak-wz-totals');
    const add = button('+ Hizmet satırı', { variant: 'ghost', onClick: () => {
      if (state.lines.length >= W.MAX_LINES) return;
      state.lines.push(lineFromService(null));
      made.paint();
      refresh();
      table.querySelector('.ak-wz-row:last-child select, .ak-wz-row:last-child input')?.focus();
    } });
    made.content.append(table, h('div', 'ak-wz-tools'), totals);
    made.content.children[1].append(add);
    const rowTotals = [];

    made.paint = () => {
      rowTotals.length = 0;
      const header = h('div', 'ak-wz-row head');
      for (const label of ['Hizmet', 'Tür (MEB kalemi)', 'Birim fiyat', 'Miktar', 'Burs %', 'İnd. %', 'İnd. TL', 'Toplam', '']) {
        header.append(h('span', label === 'Toplam' || label === 'Birim fiyat' || label === 'İnd. TL' ? 'ak-wz-num' : '', label));
      }
      table.replaceChildren(header);
      state.lines.forEach((line, index) => table.append(lineRow(line, index)));
    };

    function lineRow(line, index) {
      const row = h('div', 'ak-wz-row');
      const nameCell = h('div', 'ak-wz-name');
      const name = textInput(line.name, (value) => { line.name = value; refresh(); },
        { placeholder: 'Hizmet adı (ör. GRUP DERSİ)', aria: `${index + 1}. satır hizmet adı`, key: 'name' });
      if (services.length) {
        const options = [['', '— Serbest satır —'], ...services.map((item) => [item.id, item.name])];
        const pick = selectOf(options, line.serviceId || '', (value) => {
          const service = services.find((item) => item.id === value) || null;
          const keep = { quantity: line.quantity, scholarshipPercent: line.scholarshipPercent,
            discountPercent: line.discountPercent, extraDiscountMinor: line.extraDiscountMinor };
          Object.assign(line, lineFromService(service), keep);
          if (!service) line.name = '';
          made.paint();
          refresh();
          table.children[index + 1]?.querySelector(service ? '[data-field="unitPriceMinor"]' : '[data-field="name"]')?.focus();
        }, { aria: `${index + 1}. satır hizmet` });
        nameCell.append(pick);
        name.hidden = Boolean(line.serviceId);
      }
      nameCell.append(name);
      const category = selectOf(Object.entries(MEB_FEE_CATEGORIES), line.category, (value) => {
        line.category = value;
        refresh();
      }, { aria: `${index + 1}. satır tür` });
      const unit = moneyInput(line.unitPriceMinor, (value) => { line.unitPriceMinor = value; refresh(); },
        { key: 'unitPriceMinor', aria: `${index + 1}. satır birim fiyat` });
      const qty = textInput(line.quantity, (value) => { line.quantity = value.trim(); refresh(); },
        { mode: 'decimal', max: 8, aria: `${index + 1}. satır miktar`, key: 'quantity' });
      const burs = textInput(line.scholarshipPercent, (value) => { line.scholarshipPercent = value.trim() || '0'; refresh(); },
        { mode: 'decimal', max: 6, aria: `${index + 1}. satır burs yüzdesi`, key: 'scholarshipPercent' });
      const pct = textInput(line.discountPercent, (value) => { line.discountPercent = value.trim() || '0'; refresh(); },
        { mode: 'decimal', max: 6, aria: `${index + 1}. satır indirim yüzdesi`, key: 'discountPercent' });
      const extra = moneyInput(line.extraDiscountMinor || null, (value) => { line.extraDiscountMinor = value || 0; refresh(); },
        { aria: `${index + 1}. satır indirim tutarı`, key: 'extraDiscountMinor' });
      for (const node of [qty, burs, pct]) node.classList.add('ak-wz-num');
      const total = h('span', 'ak-wz-total', '—');
      rowTotals[index] = { total, inputs: { unit, qty, burs, pct, extra } };
      const remove = removeButton(`${index + 1}. satırı sil`, () => {
        state.lines.splice(index, 1);
        if (!state.lines.length) state.lines.push(lineFromService(null));
        made.paint();
        refresh();
      });
      row.append(nameCell, category, unit, qty, burs, pct, extra, total, remove);
      return row;
    }

    made.update = () => {
      state.lines.forEach((line, index) => {
        const slot = rowTotals[index];
        if (!slot) return;
        const result = lineTotals(line);
        slot.total.textContent = W.moneyText(result.totalMinor);
        slot.inputs.qty.classList.toggle('bad', result.errors.includes('quantity'));
        slot.inputs.burs.classList.toggle('bad', result.errors.includes('scholarshipPercent'));
        slot.inputs.pct.classList.toggle('bad', result.errors.includes('discountPercent'));
        slot.inputs.extra.classList.toggle('bad', result.errors.includes('discountExceeds'));
      });
      const sum = linesSummary(state.lines);
      totals.replaceChildren(h('span', '', 'Tutar: '), h('b', '', W.moneyText(sum.amountMinor)),
        h('span', '', 'İndirim: '), h('b', '', W.moneyText(sum.discountMinor)),
        h('span', '', 'Sözleşme tutarı: '), h('b', '', W.moneyText(sum.totalMinor)));
      add.disabled = state.lines.length >= W.MAX_LINES;
    };
    made.paint();
    return made;
  }

  // ---- 3. Ödeme ---------------------------------------------------------------------------
  function paymentSection() {
    const made = withNo(section('payment', 3, 'Ödeme'));
    const headSlot = h('div', 'ak-stack');
    const tools = h('div', 'ak-wz-tools');
    const table = h('div', 'ak-wz-table ak-wz-inst');
    table.dataset.enterSkip = '1';
    made.content.append(headSlot, tools, table);
    let rowDestroyers = [];
    const methodOptions = Object.entries(INSTALLMENT_METHODS);

    const splitBtn = button('Eşit böl', { onClick: () => { W.resplit(state); paintRows(); refresh(); },
      title: 'Kalan tutarı taksitlere eşit böler (kuruş farkı son taksitte).' });
    const settleBtn = button('Farkı son taksite ekle', { variant: 'ghost', onClick: () => {
      if (!W.settleOnLast(state)) app.toast('Fark son taksitle kapatılamıyor (taksit eksiye düşer).', 'warn');
      paintRows();
      refresh();
    } });
    const addRow = button('+ Taksit', { variant: 'ghost', onClick: () => { W.addInstallment(state); paintHead(); paintRows(); refresh(); } });

    function paintHead() {
      made.destroyers.splice(0).forEach((fn) => fn());
      headSlot.replaceChildren();
      tools.replaceChildren();
      const payment = state.payment;
      if (payment.locked) {
        made.hint.textContent = 'Plandan okunur (salt okunur). Değişiklik Kurum finansı → cari hesaptan yapılır.';
        return;
      }
      made.hint.textContent = payment.mode === 'cash' ? 'Peşin: tek ödeme.'
        : 'Taksit sayısı ve ilk vadeyi yazın — taksitler kendiliğinden bölünür; satırlar düzenlenebilir.';
      const mode = h('div', 'ak-wz-mode');
      mode.setAttribute('role', 'group');
      mode.setAttribute('aria-label', 'Ödeme biçimi');
      for (const [value, label] of [['installment', 'Taksitli'], ['cash', 'Peşin']]) {
        const chip = h('button', `kit-chip${payment.mode === value ? ' on' : ''}`, label);
        chip.type = 'button';
        chip.setAttribute('aria-pressed', payment.mode === value ? 'true' : 'false');
        chip.addEventListener('click', () => {
          if (payment.mode === value) return;
          W.setPayment(state, { mode: value });
          paintHead();
          paintRows();
          refresh();
        });
        mode.append(chip);
      }
      const grid = h('div', 'ak-wz-grid');
      if (payment.mode === 'cash') {
        const due = dateOf(payment.installments?.[0]?.dueOn || state.contractDate, (iso) => {
          if (iso === payment.installments?.[0]?.dueOn) return;
          payment.cashDue = iso;
          W.applyAuto(state);
          refresh();
        }, 'Ödeme tarihi', made);
        const method = selectOf(methodOptions, payment.method, (value) => { W.setMethodAll(state, value); refresh(); },
          { aria: 'Ödeme türü' });
        grid.append(field('Ödeme tarihi', due), field('Tür', method));
        headSlot.append(mode, grid);
        return;
      }
      const down = payment.downPayment || { amountMinor: 0, dueOn: state.contractDate, method: 'cash' };
      const downAmount = moneyInput(down.amountMinor || null, (value) => {
        W.setDownPayment(state, { amountMinor: value || 0 });
        paintRowsIfAuto();
        refresh();
      }, { aria: 'Peşinat tutarı', key: 'downPayment.amountMinor' });
      const downDue = dateOf(down.dueOn, (iso) => { W.setDownPayment(state, { dueOn: iso }); refresh(); },
        'Peşinat tarihi', made);
      made.downDue = downDue;
      const downMethod = selectOf(methodOptions, down.method || 'cash', (value) => {
        W.setDownPayment(state, { method: value });
        refresh();
      }, { aria: 'Peşinat türü' });
      const count = textInput(String(payment.count || ''), (value) => {
        const number = Number(value);
        if (!Number.isInteger(number) || number < 1 || number > 36) {
          count.classList.toggle('bad', value.trim() !== '');
          return;
        }
        count.classList.remove('bad');
        W.setPayment(state, { count: number });
        paintRows();
        refresh();
      }, { mode: 'numeric', max: 2, aria: 'Taksit sayısı', key: 'count' });
      count.classList.add('ak-wz-num');
      made.countInput = count;
      const first = dateOf(payment.firstDue, (iso) => { W.setPayment(state, { firstDue: iso }); paintRows(); refresh(); },
        'İlk vade', made);
      made.firstDue = first;
      const interval = selectOf(Object.entries(INTERVALS), payment.interval || 'monthly', (value) => {
        W.setPayment(state, { interval: value });
        paintRows();
        refresh();
      }, { aria: 'Aralık' });
      const method = selectOf(methodOptions, payment.method || 'cash', (value) => {
        W.setMethodAll(state, value);
        paintRows();
        refresh();
      }, { aria: 'Taksit türü (hepsi)' });
      grid.append(field('Peşinat', downAmount), field('Peşinat tarihi', downDue), field('Peşinat türü', downMethod),
        field('Taksit sayısı', count), field('İlk vade', first), field('Aralık', interval), field('Taksit türü', method));
      headSlot.append(mode, grid);
      tools.append(splitBtn, settleBtn, h('span', 'kit-spacer'), addRow);
    }

    // Satırlar yalnız tutar/vade/sayı DEĞİŞTİYSE yeniden çizilir (satırdaki tür seçilirken odak kaybolmasın).
    let painted = '';
    const rowsKey = () => JSON.stringify([state.payment.mode, state.payment.locked,
      (state.payment.installments || []).map((row) => [row.no, row.dueOn, row.amountMinor])]);

    function paintRowsIfAuto() {
      if ((state.payment.auto || state.payment.mode === 'cash') && rowsKey() !== painted) paintRows();
    }

    function paintRows() {
      rowDestroyers.splice(0).forEach((fn) => fn());
      painted = rowsKey();
      const payment = state.payment;
      const header = h('div', 'ak-wz-row head');
      for (const label of ['No', 'Vade', 'Tutar', 'Tür', '']) header.append(h('span', label === 'Tutar' ? 'ak-wz-num' : '', label));
      table.replaceChildren(header);
      if (payment.locked) {
        const rows = [payment.downPayment ? { ...payment.downPayment, no: 'P' } : null, ...(payment.installments || [])].filter(Boolean);
        for (const row of rows) {
          const line = h('div', 'ak-wz-row ak-wz-ro');
          line.append(h('span', '', row.no === 'P' ? 'Peş.' : String(row.no)), h('span', '', fmtDate(row.dueOn)),
            h('span', 'ak-wz-num', W.moneyText(row.amountMinor)), h('span', '', INSTALLMENT_METHODS[row.method] || '—'),
            h('span'));
          table.append(line);
        }
        return;
      }
      if (payment.mode === 'cash') {
        table.hidden = true;
        return;
      }
      table.hidden = false;
      (payment.installments || []).forEach((row, index) => {
        const line = h('div', 'ak-wz-row');
        const due = dateField({ value: row.dueOn || '', label: `${row.no}. taksit vadesi`, onChange: (iso) => {
          W.editInstallment(state, index, { dueOn: iso });
          refresh();
        } });
        rowDestroyers.push(() => due.destroy());
        const amount = moneyInput(row.amountMinor, (value) => {
          W.editInstallment(state, index, { amountMinor: value ?? 0 });
          refresh();
        }, { aria: `${row.no}. taksit tutarı`, key: 'installmentAmount' });
        const method = selectOf(methodOptions, row.method || 'cash', (value) => {
          W.editInstallment(state, index, { method: value });
          refresh();
        }, { aria: `${row.no}. taksit türü` });
        const remove = removeButton(`${row.no}. taksiti sil`, () => {
          W.removeInstallment(state, index);
          if (made.countInput) made.countInput.value = String(state.payment.count);
          paintRows();
          refresh();
        });
        line.append(h('span', '', String(row.no)), due.node, amount, method, remove);
        table.append(line);
      });
    }

    made.paint = () => {
      paintHead();
      paintRows();
    };
    made.update = () => {
      const payment = state.payment;
      if (payment.locked) return;
      const { diffMinor } = W.paymentDiff(state);
      splitBtn.disabled = payment.auto && !diffMinor;
      settleBtn.hidden = !diffMinor || payment.mode === 'cash' || !(payment.installments || []).length;
    };
    made.lineChanged = () => paintRowsIfAuto();
    made.paint();
    cleanup.push(() => rowDestroyers.splice(0).forEach((fn) => fn()));
    return made;
  }

  // ---- 4. Geçmiş ödemeler -----------------------------------------------------------------
  function pastSection() {
    const made = withNo(section('past', 4, 'Geçmiş ödemeler',
      'Mevcut öğrencinin daha önce ödedikleri (aktarım). Veliye bildirim gitmez.'));
    const table = h('div', 'ak-wz-table ak-wz-past');
    table.dataset.enterSkip = '1';
    const sum = h('div', 'ak-wz-totals');
    const tools = h('div', 'ak-wz-tools');
    let rowDestroyers = [];
    const add = button('+ Ödeme', { variant: 'ghost', onClick: () => {
      if (state.pastPayments.length >= W.MAX_PAST) return;
      state.pastPayments.push({ receivedOn: '', amountMinor: null, method: 'cash', note: '' });
      paint();
      refresh();
      table.querySelector('.ak-wz-row:last-child input')?.focus();
    } });
    const fill = button('Vadesi gelenleri ödendi say', { title: 'Peşinat ve vadesi bugün ya da önce olan taksitler '
      + 'ödenmiş olarak eklenir (tarih = vade). Sonra satırları düzeltebilirsiniz.', onClick: () => {
      const rows = W.duePastPayments(state, today);
      if (!rows.length) {
        app.toast('Vadesi gelmiş taksit yok.', 'info');
        return;
      }
      state.pastPayments = rows;
      paint();
      refresh();
      app.toast(`${rows.length} ödeme eklendi — kontrol edin.`, 'good');
    } });
    tools.append(add, fill);
    made.content.append(table, tools, sum);

    function paint() {
      rowDestroyers.splice(0).forEach((fn) => fn());
      const header = h('div', 'ak-wz-row head');
      for (const label of ['Tarih', 'Tutar', 'Yöntem', 'Not', '']) header.append(h('span', label === 'Tutar' ? 'ak-wz-num' : '', label));
      table.replaceChildren(header);
      table.hidden = !state.pastPayments.length;
      state.pastPayments.forEach((row, index) => {
        const line = h('div', 'ak-wz-row');
        const date = dateField({ value: row.receivedOn || '', label: `${index + 1}. ödeme tarihi`, onChange: (iso) => {
          row.receivedOn = iso;
          refresh();
        } });
        rowDestroyers.push(() => date.destroy());
        const amount = moneyInput(row.amountMinor, (value) => { row.amountMinor = value; refresh(); },
          { aria: `${index + 1}. ödeme tutarı` });
        const method = selectOf(Object.entries(PAST_PAYMENT_METHODS), row.method || 'cash', (value) => { row.method = value; },
          { aria: `${index + 1}. ödeme yöntemi` });
        const note = textInput(row.note || '', (value) => { row.note = value; }, { max: 200, aria: `${index + 1}. ödeme notu` });
        const remove = removeButton(`${index + 1}. ödemeyi sil`, () => {
          state.pastPayments.splice(index, 1);
          paint();
          refresh();
        });
        line.append(date.node, amount, method, note, remove);
        table.append(line);
      });
    }
    made.paint = paint;
    made.update = () => {
      const paid = state.pastPayments.reduce((total, row) => total + (Number(row.amountMinor) > 0 ? Number(row.amountMinor) : 0), 0);
      const contract = W.paymentDiff(state).contractMinor;
      sum.replaceChildren(h('span', '', 'Ödenmiş: '), h('b', '', W.moneyText(paid)),
        h('span', '', 'Kalan: '), h('b', '', W.moneyText(Math.max(0, contract - paid))));
      add.disabled = state.pastPayments.length >= W.MAX_PAST;
    };
    paint();
    cleanup.push(() => rowDestroyers.splice(0).forEach((fn) => fn()));
    return made;
  }

  // ---- 5. Sözleşme ------------------------------------------------------------------------
  function contractSection() {
    const made = withNo(section('contract', 5, 'Sözleşme'));
    const reasons = h('div', 'ak-chips');
    for (const [value, label] of Object.entries(DISCOUNT_REASONS)) {
      const chip = h('button', `kit-chip${state.discountReasons.includes(value) ? ' on' : ''}`, label);
      chip.type = 'button';
      chip.tabIndex = -1;
      chip.setAttribute('aria-pressed', state.discountReasons.includes(value) ? 'true' : 'false');
      chip.addEventListener('click', () => {
        const on = !state.discountReasons.includes(value);
        state.discountReasons = on ? [...state.discountReasons, value] : state.discountReasons.filter((item) => item !== value);
        chip.classList.toggle('on', on);
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
        refresh();
      });
      reasons.append(chip);
    }
    const note = textInput(state.discountNote, (value) => { state.discountNote = value; },
      { max: 200, placeholder: 'İsteğe bağlı (ör. kardeşi 8-A\'da)', aria: 'İndirim notu' });
    const special = h('textarea', 'kit-input ak-wz-textarea');
    special.value = state.specialClauses;
    special.placeholder = 'Her satır bir madde (isteğe bağlı)';
    special.setAttribute('aria-label', 'MEB özel hususlar');
    special.addEventListener('input', () => { state.specialClauses = special.value; refresh(); });

    const toggle = h('label', 'ak-wz-toggle');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = Boolean(state.existingSignedOn);
    check.dataset.field = 'existingSignedOn';
    const toggleText = h('div');
    toggleText.append(h('b', '', 'Basılı imzalı sözleşmesi var'), h('small'));
    toggle.append(check, toggleText);
    const signedGrid = h('div', 'ak-wz-grid');
    const number = textInput(state.existingNumber, (value) => { state.existingNumber = value; refresh(); },
      { max: 20, placeholder: 'ör. 72128', aria: 'Eski sözleşme no', key: 'existingNumber' });
    const signedOn = dateOf(state.existingSignedDate, (iso) => {
      if (iso === state.existingSignedDate) return;
      state.existingSignedDate = iso;
      state.signedDateLinked = false;
      refresh();
    }, 'İmza tarihi', made);
    signedGrid.append(field('Eski sözleşme no', number), field('İmza tarihi', signedOn));
    made.numberInput = number;

    function paintToggle() {
      const on = Boolean(state.existingSignedOn);
      toggle.classList.toggle('on', on);
      signedGrid.hidden = !on;
      const hint = nextNumberHint(settings || {});
      toggleText.querySelector('small').textContent = on
        ? 'Sözleşme eski numarasıyla İMZALI kaydedilir (yeniden imza yok); veli uygulamada hemen görür, bildirim gitmez.'
        : `Sözleşme “İmza bekliyor” olur${hint ? ` (${hint})` : ''}. “Kaydet ve yazdır” kayıt setini yazıcıya gönderir.`;
      printBtn.hidden = on;
      confirmBtn.className = `kit-btn${on ? ' kit-btn-primary' : ''}`;
      printBtn.className = 'kit-btn kit-btn-primary';
      keys.textContent = `Enter: sonraki alan · Ctrl+Enter: ${on ? 'onayla' : 'kaydet ve yazdır'} · Esc: kapat`;
    }
    check.addEventListener('change', () => {
      state.existingSignedOn = check.checked;
      paintToggle();
      refresh();
      if (check.checked) number.focus();
    });

    // Eski sözleşme aktarımında asıl alan numaradır: işaret + no + imza tarihi önce, isteğe bağlılar sonra.
    made.content.append(
      toggle,
      signedGrid,
      field('İndirim / burs nedeni', reasons),
      field('İndirim notu', note),
      field('MEB özel hususlar', special),
    );
    made.syncDates = () => { if (state.signedDateLinked) signedOn.set(state.existingSignedDate); };
    made.update = () => {};
    paintToggle();
    return made;
  }

  // ------------------------------------------------------------ canlı hesap
  function refresh() {
    if (!state) return;
    W.applyAuto(state);
    sections.lines?.update?.();
    sections.payment?.lineChanged?.();
    sections.payment?.update?.();
    sections.past?.update?.();
    const ready = W.readiness(state, { today, existing });
    const totals = ready.totals;
    const diff = W.diffText(totals.diffMinor);
    sumLine.replaceChildren(
      h('span', '', 'Sözleşme tutarı'), h('b', '', W.moneyText(totals.contractMinor)),
      h('span', '', state.payment.mode === 'cash' ? 'Ödeme' : 'Peşinat + taksitler'), h('b', '', W.moneyText(totals.paidMinor)),
      badge(diff.text, diff.tone),
    );
    const blocked = totals.contractMinor <= 0 || totals.diffMinor !== 0;
    for (const node of [confirmBtn, printBtn]) {
      node.disabled = busy || blocked;
      node.title = blocked ? 'Peşinat + taksitlerin toplamı sözleşme tutarına eşit olmalı.' : '';
    }
    // Önizleme (A5) ayrı izin: yoksa düğme gerekçesiyle kapalı.
    const canPreview = app.api.can('admin_previewContract');
    previewBtn.disabled = busy || totals.contractMinor <= 0 || !canPreview;
    previewBtn.title = canPreview ? 'Kayıt sözleşmesinin PDF önizlemesi (kayıt yazılmaz).'
      : 'Sözleşme önizleme yetkiniz yok.';
    const missing = missingFor(partyResp, state.payerGuardianSlot);
    printBtn.title = printBtn.title || (missing.length ? 'Eksik kimlik bilgisi var: belgeler üretilemeyebilir.' : '');
    paintErrors(ready);
  }

  function paintErrors(ready) {
    const bySection = {};
    if (showErrors) for (const [key, list] of Object.entries(ready.bySection)) bySection[key] = [...list];
    for (const item of serverFields) (bySection[item.section] ||= []).push(item.message);
    for (const [key, made] of Object.entries(sections)) {
      if (!made) continue;
      const list = [...new Set(bySection[key] || [])];
      made.errors.replaceChildren(...list.map((text) => h('div', '', text)));
      made.node.classList.toggle('bad', list.length > 0);
    }
  }

  // ------------------------------------------------------------ önizle / onayla
  async function preview() {
    if (!state || busy) return;
    errorLine.textContent = '';
    const body0 = W.buildDraft(state);
    setBusy(true, previewBtn, 'Hazırlanıyor…');
    try {
      const result = await app.api.call('POST', paths.preview(studentId), { query: { pdf: 'registration' },
        body: body0, key: W.createKey(`${sessionKey}-p`, body0) });
      const data = result.data || {};
      const warnings = [...(data.warnings || []).map((item) => item.message),
        ...(data.missingFields || []).map((item) => `Eksik: ${missingText(item)}`)];
      if (warnings.length) errorLine.textContent = warnings.join(' · ');
      if (data.file?.url) {
        await openContractFile(app, { kind: KM_PDF_KINDS.preview, url: data.file.url,
          name: data.file.fileName || 'sozlesme-onizleme', opener: previewBtn });
      } else if (!warnings.length) {
        app.toast('Önizleme belgesi üretilmedi (eksik bilgi olabilir).', 'warn');
      }
    } catch (failure) {
      showServerError(failure);
    } finally {
      setBusy(false, previewBtn, 'Önizle');
    }
  }

  async function submit({ print }) {
    if (!state || busy || closed) return;
    showErrors = true;
    serverFields = [];
    errorLine.textContent = '';
    const ready = W.readiness(state, { today, existing });
    refresh();
    if (!ready.ok) {
      errorLine.textContent = `Eksik ya da hatalı: ${ready.errors.slice(0, 3).map((item) => item.message).join(' · ')}`
        + `${ready.errors.length > 3 ? ` (+${ready.errors.length - 3})` : ''}`;
      sections[ready.errors[0].section]?.node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      return;
    }
    const button0 = print ? printBtn : confirmBtn;
    setBusy(true, button0, 'Kaydediliyor…');
    let detail;
    try {
      detail = await app.api.post(paths.studentContracts(studentId), ready.body,
        { key: W.createKey(sessionKey, ready.body) });
    } catch (failure) {
      setBusy(false, button0, print ? 'Kaydet ve yazdır' : 'Onayla');
      showServerError(failure);
      return;
    }
    const contractId = detail?.id;
    // Ortak yol `bbd-akademi:contract-changed` yayınlar (80-km-belge ekranı + öğrencinin Sözleşmeler sekmesi dinler).
    emitContractEvent({ type: 'contract.changed', contractId, studentId, change: 'created' });
    app.api.invalidate?.('/v1/admin/finance');
    const number = detail?.number || '';
    const signed = Boolean(ready.body.existingSigned);
    app.toast(signed ? `Sözleşme ${number} imzalı kaydedildi; taksit planı ve ödemeler işlendi.`
      : `Sözleşme ${number} oluşturuldu (imza bekliyor); taksit planı işlendi.`, 'good');
    close(detail);
    if (print && contractId) {
      await buildDocuments(app, W.documentsBody(contractId, settings || undefined),
        { print: true, key: newKey('km-ctr-doc'), name: `${number || 'sozlesme'}-kayit-seti` });
    }
  }

  function showServerError(failure) {
    const parsed = W.serverProblem(failure, contractProblem(failure));
    serverFields = parsed.fields || [];
    errorLine.textContent = parsed.summary || problem(failure, 'Sözleşme kaydedilemedi.');
    if (failure?.code === 'contract_number_taken') sections.contract?.numberInput?.focus();
    if (failure?.code === 'contract_missing_fields') reloadParty();
    refresh();
    const first = serverFields[0]?.section;
    if (first) sections[first]?.node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function setBusy(on, node, label) {
    busy = on;
    node.textContent = label;
    if (!on) refresh();
    else for (const item of [confirmBtn, printBtn, previewBtn, cancelBtn]) item.disabled = true;
    if (!on) cancelBtn.disabled = false;
  }

  cleanup.push(() => {
    for (const made of Object.values(sections)) made?.destroyers?.splice(0).forEach((fn) => fn());
  });

  return {
    focus: () => {
      if (!overlay.isConnected) return;
      (card.querySelector('input:focus, select:focus') || fields()[0] || card).focus();
    },
  };
}
