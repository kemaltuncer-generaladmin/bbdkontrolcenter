// BBD Akademi — "Kayıt ve ödeme planı" sihirbazının SAF mantığı (F-SZ1, iş 80, 80-km-sihirbaz).
//
// DOM'suz; yalnız ortak saf mantığı (`shared-contract-logic.js`, 80-km-sozlesme) içe aktarır. Node'da sınanır
// (`tests/test_akademi_sozlesme_sihirbaz.py`). Satır toplamı, eşit bölme ve toplam denetimi ORTAKTADIR (çoğaltılmaz);
// burada sihirbaza özgü olanlar durur:
//   · akıllı varsayılanlar — ilk vade = gelecek ayın sözleşme günü, taksit türü NAKİT, eğitim yılı önerisi, peşinat
//     ve imza tarihi sözleşme tarihini izler (kullanıcı o alanı elle değiştirene dek), "Basılı imzalı" varsayılanı
//     (mevcut öğrencide AÇIK, "Yeni öğrenci"den gelende KAPALI — K6);
//   · canlı eşit bölme (taksit satırı elle düzeltilince durur; "Eşit böl" yeniden açar), peşin modda tek ödeme;
//   · mevcut plandan ("Sözleşme oluştur", planId) satır ve ödeme bölümü;
//   · geçmiş ödemelerin "vadesi gelenleri ödendi say" kısayolu (eski sözleşmeyi < 1 dakikada aktarma);
//   · gövde (`ContractDraft`) + gövdeye bağlı Idempotency-Key + kayıt seti belge isteği (A17);
//   · olay ayrıntısından öğrenci kimliği (Akademi UUID'si ya da KM `STU-…` numarası).
//
// SUNUCU ESASTIR: Akademi aynı hesabı yapar; tutmazsa `contract_totals_mismatch` (422) döner.

import {
  DEFAULT_SETTINGS, addInterval, defaultPayerSlot, draftBody, hashText, lineFromService, paymentFromPlan, renumber,
  splitEqual, suggestAcademicYear, totalsCheck, validateDraft,
} from './shared-contract-logic.js';

/** Sihirbazın açılış kaynağı → "Basılı imzalı" varsayılanı ve geçmiş ödemeler bölümü buna bağlı. */
export const SOURCES = {
  new: 'Yeni öğrenci',            // KM "Yeni öğrenci" → kayıt sonrası adım (bbd-students:created)
  existing: 'Mevcut öğrenci',     // finans hesabı / öğrenci kartı düğmesi
  missing: 'Sözleşmesi olmayan',  // "Sözleşmesi olmayanlar" listesi
  plan: 'Mevcut plan',            // planı olup sözleşmesi olmayan ("Sözleşme oluştur", planId)
};

export const DEFAULT_COUNT = 8;
export const DEFAULT_METHOD = 'cash';
export const MAX_LINES = 30;
export const MAX_PAST = 60;
export const MAX_SPECIAL = 20;

/** Taksit türü → geçmiş tahsilat yöntemi (`payments.method`). Senet/çek çoğunlukla nakit ödenir. */
const PAST_METHOD_OF = { cash: 'cash', transfer: 'transfer', card: 'card', online_card: 'card', promissory: 'cash',
  cheque: 'cash' };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KM_REF = /^STU-[A-Za-z0-9]+$/i;

/** "Basılı imzalı sözleşmesi var" varsayılanı: yalnız KM "Yeni öğrenci" akışında KAPALI (K6). */
export function printedSignedDefault(source) {
  return source !== 'new';
}

/** İlk vade önerisi: sözleşme tarihinin GELECEK ayındaki aynı gün (ay sonu taşmaz: 31.01 → 28/29.02). */
export function firstDueSuggestion(contractDate) {
  return contractDate ? addInterval(contractDate, 'monthly', 1) : '';
}

/** A4 yanıtından ödeme sorumlusu: sunucunun seçtiği `party.guardian` → ortak kural (ödeme yetkili birincil …). */
export function payerSlotOf(partyResponse = {}) {
  const party = partyResponse?.party || {};
  const guardians = party.guardians || [];
  const chosen = party.guardian?.slot;
  if (chosen && guardians.some((item) => item.slot === chosen)) return chosen;
  return defaultPayerSlot(partyResponse);
}

/** Planın iptal edilmemiş kalemleri. */
function liveCharges(plan = {}) {
  return (plan.charges || []).filter((row) => row.status !== 'cancelled');
}

/**
 * Mevcut plandan tek hizmet satırı (plan başlığı + tutarı). Akademi'de `totalMinor` İNDİRİM SONRASIDIR; indirim varsa
 * belgeye liste fiyatı + indirim TL olarak geçer. Satır toplamı her durumda planın canlı kalem toplamına eşittir.
 */
export function planLine(plan = {}) {
  const charges = liveCharges(plan);
  const total = charges.length ? charges.reduce((sum, row) => sum + (Number(row.amountMinor) || 0), 0)
    : Number(plan.totalMinor) || 0;
  const discount = Math.max(0, Number(plan.discount?.amountMinor) || 0);
  let unit = Number(plan.discount?.listPriceMinor) || 0;
  if (!discount || unit - discount !== total) unit = total + discount;
  return {
    ...lineFromService(null),
    name: String(plan.title || 'Eğitim ücreti').slice(0, 120),
    unitPriceMinor: unit,
    extraDiscountMinor: discount,
  };
}

/** Mevcut plandan ödeme bölümü (salt okunur; sunucu planı bağlar). Eski planda tür boşsa NAKİT. */
export function planPayment(plan = {}) {
  const payment = paymentFromPlan({ ...plan, charges: liveCharges(plan) });
  const fill = (row) => (row ? { ...row, method: row.method && row.method !== 'promissory' ? row.method
    : (liveCharges(plan).find((item) => item.dueDate === row.dueOn)?.plannedMethod || DEFAULT_METHOD) } : row);
  return { ...payment, downPayment: fill(payment.downPayment), installments: payment.installments.map(fill),
    locked: true };
}

/** Katalogdan ilk etkin hizmet (sıra → ad). */
export function firstService(services = []) {
  return [...(services || [])].filter((item) => item && item.active !== false)
    .sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0) || String(a.name).localeCompare(String(b.name), 'tr'))[0]
    || null;
}

/**
 * Sihirbazın ilk durumu (akıllı varsayılanlar).
 * @param {object} spec
 *   source   SOURCES anahtarı
 *   today    'YYYY-MM-DD' (Europe/Istanbul)
 *   party    A4 yanıtı ({party, missingFields, identityUpdatedAt})
 *   settings `contractSettingsOf` çıktısı
 *   plan     "Sözleşme oluştur" planı (FinanceInstallmentPlan) ya da null
 *   services A19 kataloğu
 */
export function initialState({ source = 'existing', today, party = {}, settings = DEFAULT_SETTINGS, plan = null,
  services = [] } = {}) {
  const contractDate = today;
  const registered = party?.party?.student?.registeredOn || '';
  const service = firstService(services);
  const state = {
    source,
    planId: plan?.id || null,
    academicYear: suggestAcademicYear(today, settings?.academicYearStartMonth || 9),
    yearLinked: true,
    contractDate,
    registeredOn: registered || contractDate,
    registeredLinked: !registered,
    payerGuardianSlot: payerSlotOf(party),
    lines: plan ? [planLine(plan)] : [lineFromService(service)],
    payment: plan ? planPayment(plan) : {
      mode: 'installment',
      downPayment: { amountMinor: 0, dueOn: contractDate, method: DEFAULT_METHOD },
      downLinked: true,
      count: DEFAULT_COUNT,
      firstDue: firstDueSuggestion(contractDate),
      firstDueLinked: true,
      interval: 'monthly',
      method: DEFAULT_METHOD,
      auto: true,
      installments: [],
    },
    pastPayments: [],
    discountReasons: [],
    discountNote: '',
    specialClauses: '',
    existingSignedOn: printedSignedDefault(source),
    existingNumber: '',
    existingSignedDate: contractDate,
    signedDateLinked: true,
  };
  return applyAuto(state);
}

/**
 * Canlı hesap: peşin modda tek ödeme = sözleşme tutarı; taksitli modda "eşit böl" açıkken taksitler (sözleşme −
 * peşinat) yeniden bölünür. Plan bağlıysa (salt okunur) dokunulmaz. Durumu YERİNDE günceller ve döndürür.
 */
export function applyAuto(state) {
  const payment = state.payment || {};
  if (payment.locked) return state;
  const contract = totalsCheck({ lines: state.lines, payment: { mode: 'cash', installments: [] } }).contractMinor;
  if (payment.mode === 'cash') {
    payment.installments = [{ no: 1, dueOn: payment.cashDue || state.contractDate, amountMinor: contract,
      method: payment.method || DEFAULT_METHOD }];
    return state;
  }
  if (payment.auto) {
    const down = Math.max(0, Number(payment.downPayment?.amountMinor) || 0);
    payment.installments = splitEqual(Math.max(0, contract - down), payment.count, payment.firstDue,
      { interval: payment.interval || 'monthly', method: payment.method || DEFAULT_METHOD });
  }
  return state;
}

/**
 * Sözleşme tarihi değişti: ona bağlı (elle değiştirilmemiş) alanlar izler — kayıt tarihi, peşinat tarihi, ilk vade,
 * eğitim yılı, eski sözleşmenin imza tarihi.
 */
export function setContractDate(state, iso, { startMonth = 9 } = {}) {
  if (iso === state.contractDate) return state;
  state.contractDate = iso;
  if (state.registeredLinked) state.registeredOn = iso;
  if (state.signedDateLinked) state.existingSignedDate = iso;
  if (state.yearLinked && iso) state.academicYear = suggestAcademicYear(iso, startMonth);
  const payment = state.payment || {};
  if (!payment.locked) {
    if (payment.downLinked && payment.downPayment) payment.downPayment.dueOn = iso;
    if (payment.firstDueLinked) payment.firstDue = firstDueSuggestion(iso);
  }
  return applyAuto(state);
}

/** Ödeme biçimi / sayı / ilk vade / aralık / peşinat değişti: taksitler yeniden bölünür (eşit böl yeniden açılır). */
export function setPayment(state, patch0 = {}) {
  const payment = state.payment;
  if (payment.locked) return state;
  // Yalnız DEĞİŞEN alanlar (tarih alanı odaktan çıkınca aynı değeri yeniden bildirir — elle düzeltmeler bozulmasın).
  const patch = Object.fromEntries(Object.entries(patch0).filter(([key, value]) => payment[key] !== value));
  if (!Object.keys(patch).length) return state;
  Object.assign(payment, patch);
  if ('firstDue' in patch) payment.firstDueLinked = false;
  if ('count' in patch || 'firstDue' in patch || 'interval' in patch || 'mode' in patch) payment.auto = true;
  return applyAuto(state);
}

/** Peşinat alanı (tutar / tarih / tür). Tutar değişince taksitler (eşit böl açıksa) yeniden bölünür. */
export function setDownPayment(state, patch = {}) {
  const payment = state.payment;
  if (payment.locked) return state;
  const before = payment.downPayment || { amountMinor: 0, dueOn: state.contractDate, method: DEFAULT_METHOD };
  if ('dueOn' in patch && patch.dueOn !== before.dueOn) payment.downLinked = false;
  payment.downPayment = { ...before, ...patch };
  return applyAuto(state);
}

/** Bütün taksitlerin türü (üstteki "Taksit türü") — yeni bölmelerde de bu tür kullanılır. */
export function setMethodAll(state, method) {
  const payment = state.payment;
  if (payment.locked) return state;
  payment.method = method;
  payment.installments = (payment.installments || []).map((row) => ({ ...row, method }));
  return applyAuto(state);
}

/** Taksit satırı elle değişti: "eşit böl" durur (satır korunur), toplam farkı canlı gösterilir. */
export function editInstallment(state, index, patch0 = {}) {
  const payment = state.payment;
  if (payment.locked || !payment.installments?.[index]) return state;
  const row0 = payment.installments[index];
  const patch = Object.fromEntries(Object.entries(patch0).filter(([key, value]) => row0[key] !== value));
  if (!Object.keys(patch).length) return state;
  const touchesAmount = 'amountMinor' in patch || 'dueOn' in patch;
  payment.installments = payment.installments.map((row, position) => (position === index ? { ...row, ...patch } : row));
  if (touchesAmount && payment.mode === 'installment') payment.auto = false;
  if (payment.mode === 'cash' && 'dueOn' in patch) payment.cashDue = patch.dueOn;
  return state;
}

/** Taksit satırı ekle (son vadeden bir aralık sonra) / sil — elle düzenleme sayılır. */
export function addInstallment(state) {
  const payment = state.payment;
  if (payment.locked || payment.mode !== 'installment') return state;
  const rows = payment.installments || [];
  const last = rows[rows.length - 1];
  const dueOn = last?.dueOn ? addInterval(last.dueOn, payment.interval || 'monthly', 1) : payment.firstDue;
  payment.installments = renumber([...rows, { no: rows.length + 1, dueOn, amountMinor: 0,
    method: payment.method || DEFAULT_METHOD }]);
  payment.count = payment.installments.length;
  payment.auto = false;
  return state;
}

export function removeInstallment(state, index) {
  const payment = state.payment;
  if (payment.locked || payment.mode !== 'installment') return state;
  payment.installments = renumber((payment.installments || []).filter((_, position) => position !== index));
  payment.count = payment.installments.length;
  payment.auto = false;
  return state;
}

/** "Eşit böl": elle düzeltmeleri bırakıp yeniden böl. */
export function resplit(state) {
  if (state.payment.locked) return state;
  state.payment.auto = true;
  state.payment.count = Math.max(1, Number(state.payment.count) || state.payment.installments?.length || DEFAULT_COUNT);
  return applyAuto(state);
}

/** "Farkı son taksite ekle": toplam farkı son taksitte kapatılır (sonuç ≤ 0 olacaksa yapılmaz → false). */
export function settleOnLast(state) {
  const payment = state.payment;
  const rows = payment.installments || [];
  if (payment.locked || !rows.length) return false;
  const { diffMinor } = paymentDiff(state);
  const last = rows[rows.length - 1];
  const next = (Number(last.amountMinor) || 0) + diffMinor;
  if (!diffMinor || next <= 0) return false;
  editInstallment(state, rows.length - 1, { amountMinor: next });
  return true;
}

/** Toplam denetimi (ortak `totalsCheck`) + taksit sayısı. */
export function paymentDiff(state) {
  const check = totalsCheck(state);
  return { ...check, count: (state.payment?.installments || []).length };
}

/** Kuruş → "1.250,30 ₺" (ICU'suz ortamda da Türkçe ayraç). */
export function moneyText(kurus) {
  const value = Math.round(Number(kurus) || 0);
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${sign}${whole},${String(abs % 100).padStart(2, '0')} ₺`;
}

/** Toplam farkı metni + tonu (alt şerit). */
export function diffText(diffMinor) {
  if (!diffMinor) return { text: 'Peşinat + taksitler sözleşme tutarına eşit.', tone: 'good' };
  return diffMinor > 0
    ? { text: `${moneyText(diffMinor)} dağıtılmadı — taksitler sözleşme tutarından az.`, tone: 'bad' }
    : { text: `${moneyText(-diffMinor)} fazla — taksitler sözleşme tutarını aşıyor.`, tone: 'bad' };
}

/**
 * "Vadesi gelenleri ödendi say": peşinat + vadesi bugün ya da önce olan taksitler geçmiş tahsilat olur (tarih = vade,
 * tutar = taksit, yöntem türden). Var olan satırlara eklenmez — yerine geçer (iki kez basılırsa çift kayıt olmasın).
 */
export function duePastPayments(state, today) {
  const payment = state.payment || {};
  const rows = [];
  const down = payment.mode === 'installment' ? payment.downPayment : null;
  if (down && Number(down.amountMinor) > 0 && down.dueOn && down.dueOn <= today) {
    rows.push({ receivedOn: down.dueOn, amountMinor: Number(down.amountMinor),
      method: PAST_METHOD_OF[down.method] || 'cash', note: 'Peşinat' });
  }
  for (const row of payment.installments || []) {
    if (!row.dueOn || row.dueOn > today || !(Number(row.amountMinor) > 0)) continue;
    rows.push({ receivedOn: row.dueOn, amountMinor: Number(row.amountMinor),
      method: PAST_METHOD_OF[row.method] || 'cash',
      note: payment.mode === 'cash' ? 'Peşin ödeme' : `${row.no}. taksit` });
  }
  return rows.slice(0, MAX_PAST);
}

/** Sihirbaz durumu → `ContractDraft` (ortak `draftBody`; özel hususlar satır satır, en çok 20). */
export function buildDraft(state) {
  const body = draftBody(state);
  if (body.specialClauses) body.specialClauses = body.specialClauses.slice(0, MAX_SPECIAL).map((item) => item.slice(0, 1000));
  if (body.existingSigned && !body.existingSigned.signedOn) body.existingSigned.signedOn = state.contractDate;
  return body;
}

/**
 * Onay öncesi: istemci doğrulaması (ortak `validateDraft`) + gövde. `ok` false iken "Onayla" kapalıdır.
 * Bölüm başına iletiler `bySection` içindedir.
 */
export function readiness(state, { today, existing = [] } = {}) {
  const errors = validateDraft(state, { today });
  const same = sameYearContract(existing, state.academicYear);
  if (same) {
    errors.push({ section: 'contract', field: 'academicYear', message: `Bu öğrencinin ${state.academicYear} için geçerli `
      + `sözleşmesi var (${same.number || 'no yok'}); aynı yıla ikinci sözleşme açılamaz — eğitim yılını değiştirin ya da `
      + 'mevcut sözleşmeyi Sözleşmeler sekmesinden düzenleyin.' });
  }
  if ((state.lines || []).length > MAX_LINES) {
    errors.push({ section: 'lines', field: 'lines', message: `En çok ${MAX_LINES} hizmet satırı.` });
  }
  if ((state.pastPayments || []).filter((row) => Number(row.amountMinor) > 0).length > MAX_PAST) {
    errors.push({ section: 'past', field: 'pastPayments', message: `En çok ${MAX_PAST} geçmiş ödeme.` });
  }
  const bySection = {};
  for (const item of errors) (bySection[item.section] ||= []).push(item.message);
  return { ok: errors.length === 0, errors, bySection, body: buildDraft(state), totals: paymentDiff(state) };
}

/** Gövdeye bağlı Idempotency-Key: aynı pencere + aynı gövde = aynı anahtar (çift tık / yeniden deneme tek kayıt). */
export function createKey(sessionKey, body) {
  return `${sessionKey}-${hashText(JSON.stringify(body))}`.slice(0, 120);
}

/** "Kaydet ve yazdır": kayıt seti (ayar `contracts.registrationKinds`) TEK PDF, nüsha `contracts.defaultCopies` (A17). */
export function documentsBody(contractId, settings = DEFAULT_SETTINGS) {
  const kinds = (settings?.registrationKinds?.length ? settings.registrationKinds : DEFAULT_SETTINGS.registrationKinds)
    .filter((kind, index, list) => list.indexOf(kind) === index);
  const copies = {};
  for (const kind of kinds) {
    const count = Number(settings?.defaultCopies?.[kind] ?? DEFAULT_SETTINGS.defaultCopies[kind] ?? 1);
    copies[kind] = Math.min(3, Math.max(1, Math.round(count) || 1));
  }
  return { contractIds: [contractId], kinds, options: { copies, promissoryScope: 'all', revision: 'latest' } };
}

/**
 * Olay ayrıntısı → öğrenci. `bbd-akademi:open-contract-wizard` Akademi UUID'si verir; `bbd-students:created`
 * KM numarası (`STU-…`, Akademi'de `externalRef`) verir — Akademi aynası gelene dek o numarayla aranır.
 */
export function eventTarget(detail = {}) {
  const values = [detail.akademiStudentId, detail.studentId, detail.externalId, detail.kantinId, detail.externalRef]
    .map((value) => (value === null || value === undefined ? '' : String(value).trim())).filter(Boolean);
  const akademiId = values.find((value) => UUID.test(value)) || null;
  const refs = [...new Set(values.filter((value) => KM_REF.test(value)).map((value) => value.toUpperCase()))];
  return { akademiId, refs, name: String(detail.displayName || detail.name || '').trim(),
    planId: detail.planId && UUID.test(String(detail.planId)) ? String(detail.planId) : null };
}

/** Öğrencinin GEÇERLİ sözleşmeleri (A3) — sihirbazın tepesindeki uyarı için. */
export function activeContracts(list) {
  const items = Array.isArray(list) ? list : (list?.items || []);
  return items.filter((item) => (item.status || 'active') === 'active');
}

/** Aynı eğitim yılında GEÇERLİ sözleşme (Akademi bunu `409 contract_exists` ile reddeder). */
export function sameYearContract(existing = [], academicYear = '') {
  return activeContracts(existing).find((item) => item.academicYear && item.academicYear === academicYear) || null;
}

/**
 * Sihirbaza özgü sunucu hataları (80-sozlesme-hazir: A6 `409 contract_exists`, `plan_already_contracted`,
 * `plan_not_active`; `contract_totals_mismatch` ayrıntısı tutarlarla). Bilinmeyen kodda ortak `contractProblem` sonucu
 * (`base`) aynen döner.
 */
export function serverProblem(error, base = { summary: '', fields: [], missing: [] }) {
  const code = error?.code || '';
  const details = error?.details && typeof error.details === 'object' ? error.details : {};
  if (code === 'contract_exists') {
    return { summary: 'Öğrencinin bu eğitim yılı için geçerli bir sözleşmesi var; ikinci sözleşme açılamaz.',
      fields: [{ section: 'contract', field: 'academicYear',
        message: 'Bu yılın sözleşmesi zaten var — eğitim yılını değiştirin ya da mevcut sözleşmeyi düzenleyin.' }],
      missing: [], contractId: details.contractId || null };
  }
  if (code === 'plan_already_contracted') {
    return { summary: 'Bu taksit planı başka bir sözleşmeye bağlı.', fields: [{ section: 'payment', field: 'planId',
      message: 'Plan zaten sözleşmeli — öğrencinin Sözleşmeler sekmesine bakın.' }], missing: [] };
  }
  if (code === 'plan_not_active') {
    return { summary: 'Bağlanacak taksit planı etkin değil (tamamlanmış ya da iptal).', fields: [{ section: 'payment',
      field: 'planId', message: 'Plan etkin değil — Kurum finansı → cari hesaptan kontrol edin.' }], missing: [] };
  }
  if (code === 'contract_totals_mismatch' && Number.isInteger(details.expectedMinor) && Number.isInteger(details.actualMinor)) {
    return { ...base, summary: `Sözleşme tutarı ${moneyText(details.expectedMinor)}, peşinat + taksitler `
      + `${moneyText(details.actualMinor)} — eşit olmalı.` };
  }
  return base;
}

/** Plan bu öğrencinin bir sözleşmesine bağlı mı? ("Sözleşme oluştur" yalnız bağsız ETKİN planda.) */
export function plansWithoutContract(plans = [], contracts = []) {
  const bound = new Set((Array.isArray(contracts) ? contracts : (contracts?.items || []))
    .filter((item) => item.status !== 'cancelled').map((item) => item.planId).filter(Boolean));
  return (plans || []).filter((plan) => plan.status === 'active' && !bound.has(plan.id));
}

/** Girilenler değişti mi (kapatırken sor)? */
export function fingerprint(state) {
  return JSON.stringify(buildDraft(state));
}
