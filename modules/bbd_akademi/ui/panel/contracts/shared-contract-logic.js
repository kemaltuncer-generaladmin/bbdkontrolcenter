// BBD Akademi — Sözleşmeler (F-SZ, iş 80): ORTAK SAF mantık (sekme, sihirbaz, Sözleşmeler ekranı). İmport'suz ve DOM'suzdur; Node'da sınanır
// (`tests/test_akademi_sozlesme_km.py`). Kullananlar: öğrenci Sözleşmeler sekmesi (`contract-*.js`, 80-km-sozlesme),
// "Kayıt ve ödeme planı" sihirbazı (`wizard-*.js`, 80-km-sihirbaz), Sözleşmeler ekranı (80-km-belge).
//
// SUNUCU ESASTIR. Satır toplamı, eşit bölme ve toplam farkı Akademi'deki kuralın (80-api-taslak "Satır ve tutar
// kuralı") istemci KOPYASIDIR: ekran yazarken canlı göstermek ve "Onayla"yı fark sıfır değilken kapalı tutmak için.
// Kayıt anında Akademi aynı hesabı yapar; tutmazsa `contract_totals_mismatch` (422) döner.
//
// Para HER YERDE kuruş tamsayıdır (A4). Yüzde ve miktar en çok iki ondalıktır; kayan nokta hatası olmasın diye
// yüzlük tamsayıya çevrilip tamsayı aritmetiğiyle yuvarlanır (yarım yukarı).
//
// Etiketler web / mobil / PDF ile AYNI (taslak "Ortak şemalar").

export const DOCUMENT_KINDS = {
  registration: 'Öğrenci Kayıt Sözleşmesi',
  meb: 'MEB Kayıt Sözleşmesi',
  promissory: 'Senetler',
  payment_plan: 'Ödeme Planı',
  legal_notice: 'Yasal Uyarı',
  // Deneme kulübü sözleşmesi (iş 81, Akademi README §14 madde 140).
  exam_club: 'Deneme Kulübü Üyelik Sözleşmesi',
  exam_club_consent: 'Başarı İlanı Açık Rıza Formu',
};
/** Sözleşme türü (Akademi `ContractType`, iş 81). */
export const CONTRACT_TYPES = { registration: 'Kayıt sözleşmesi', exam_club: 'Deneme kulübü sözleşmesi' };
/** İmzalanan (veliye görünen) türler. */
export const SIGNABLE_KINDS = ['registration', 'meb', 'promissory'];
/** Madde düzenlenebilen türler (ödeme planı maddesizdir). */
export const CLAUSE_KINDS = ['registration', 'meb', 'promissory', 'legal_notice'];

export const SIGNATURE_STATES = {
  awaiting: ['İmza bekliyor', 'warn'],
  signed: ['İmzalı', 'good'],
  resign_required: ['Yeniden imza bekliyor', 'bad'],
};
export const SIGNATURE_SOURCES = { paper: 'Islak imza', paper_existing: 'İmzalı (basılı, eski)',
  online: 'Çevrim içi onay' };
export const CONTRACT_STATUSES = {
  active: ['Geçerli', 'info'],
  cancelled: ['İptal', 'dim'],
  terminated: ['Sona erdi', 'dim'],
};
export const INSTALLMENT_METHODS = {
  cash: 'NAKİT', promissory: 'SENET', cheque: 'ÇEK', card: 'KREDİ KARTI', transfer: 'HAVALE/EFT',
  online_card: 'UYGULAMADAN KART', store: 'BBDSTORE',
};
export const MEB_FEE_CATEGORIES = {
  education: 'Eğitim', stationery: 'Kırtasiye', book: 'Kitap', meal: 'Yemek', breakfast: 'Kahvaltı',
  transport: 'Servis', uniform: 'Kıyafet', supplementary_course: 'Takviye kursu', study_hall: 'Etüt',
  dormitory: 'Yatakhane', other: 'Diğer',
};
export const DISCOUNT_REASONS = {
  scholarship: 'Burs', sibling: 'Kardeş indirimi', staff_child: 'Personel çocuğu indirimi',
  success: 'Başarı indirimi', martyr_veteran_child: 'Şehit/Gazi çocuğu indirimi', early_registration: 'Erken kayıt',
  other: 'Diğer',
};
/** Geçmiş (aktarım) tahsilatın yöntemi — mevcut `payments.method` değerleri. */
export const PAST_PAYMENT_METHODS = { cash: 'Nakit', transfer: 'Havale / EFT', card: 'Kart (POS)', other: 'Diğer' };
export const INTERVALS = { monthly: 'Aylık', biweekly: 'İki haftada bir', weekly: 'Haftalık' };
export const RELATIONS = {
  mother: 'Anne', father: 'Baba', guardian: 'Vasi', grandmother: 'Büyükanne', grandfather: 'Büyükbaba',
  sibling: 'Kardeş', other: 'Diğer',
};

/** Ayar yoksa kullanılan varsayılanlar (taslak "Ayarlar"). */
export const DEFAULT_SETTINGS = {
  numberPrefix: 'BBD1-',
  nextNumber: null,
  academicYearStartMonth: 9,
  registrationKinds: ['registration', 'meb', 'promissory', 'payment_plan'],
  defaultCopies: { registration: 2, meb: 2, promissory: 1, payment_plan: 1, legal_notice: 1 },
};

// ------------------------------------------------------------------ sayı

/** '12,5' / '12.5' / 12.5 → yüzlük tamsayı (1250). Çözülemezse null; ikiden çok ondalık null. */
export function hundredths(value) {
  if (value === null || value === undefined || value === '') return null;
  const text = String(value).trim().replace(/\s/g, '').replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, frac = ''] = text.split('.');
  return Number(whole) * 100 + Number((frac + '00').slice(0, 2));
}

/** a × b / bölen, yarım yukarı (negatif olmayan tamsayılar). */
function mulDivHalfUp(a, b, divisor) {
  return Math.floor((a * b * 2 + divisor) / (divisor * 2));
}

/**
 * Satır hesabı (sunucu kuralının kopyası):
 *   amount   = round_half_up(unitPriceMinor × quantity)
 *   discount = round_half_up(amount × burs/100) + round_half_up(amount × indirim/100) + extraDiscountMinor (≤ amount)
 *   total    = amount − discount
 * Geçersiz alan 0 sayılır, `error` alanına yazılır.
 */
export function lineTotals(line = {}) {
  const errors = [];
  const unit = Number.isInteger(line.unitPriceMinor) && line.unitPriceMinor >= 0 ? line.unitPriceMinor : 0;
  if (!(Number.isInteger(line.unitPriceMinor) && line.unitPriceMinor >= 0)) errors.push('unitPriceMinor');
  const quantity = hundredths(line.quantity ?? 1);
  if (quantity === null || quantity <= 0) errors.push('quantity');
  const scholarship = hundredths(line.scholarshipPercent ?? 0);
  if (scholarship === null || scholarship > 10000) errors.push('scholarshipPercent');
  const discountPct = hundredths(line.discountPercent ?? 0);
  if (discountPct === null || discountPct > 10000) errors.push('discountPercent');
  const extra = Number.isInteger(line.extraDiscountMinor) && line.extraDiscountMinor >= 0 ? line.extraDiscountMinor : 0;
  if (line.extraDiscountMinor !== undefined && line.extraDiscountMinor !== null
    && !(Number.isInteger(line.extraDiscountMinor) && line.extraDiscountMinor >= 0)) errors.push('extraDiscountMinor');
  const amountMinor = quantity && quantity > 0 ? mulDivHalfUp(unit, quantity, 100) : 0;
  let discountMinor = mulDivHalfUp(amountMinor, scholarship && scholarship <= 10000 ? scholarship : 0, 10000)
    + mulDivHalfUp(amountMinor, discountPct && discountPct <= 10000 ? discountPct : 0, 10000) + extra;
  if (discountMinor > amountMinor) {
    errors.push('discountExceeds');
    discountMinor = amountMinor;
  }
  return { amountMinor, discountMinor, totalMinor: amountMinor - discountMinor, errors };
}

/** Satır listesinin toplamları. */
export function linesSummary(lines = []) {
  const out = { amountMinor: 0, discountMinor: 0, totalMinor: 0 };
  for (const line of lines) {
    const row = lineTotals(line);
    out.amountMinor += row.amountMinor;
    out.discountMinor += row.discountMinor;
    out.totalMinor += row.totalMinor;
  }
  return out;
}

// ------------------------------------------------------------------ tarih

/** 'YYYY-MM-DD' + n aralık. Ay sonunu taşmaz: 31.01 + 1 ay = 28/29.02; gün İLK vadeden alınır (birikmez). */
export function addInterval(iso, interval, steps) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
  if (!year || !month || !day) return iso;
  if (interval === 'weekly' || interval === 'biweekly') {
    const date = new Date(Date.UTC(year, month - 1, day + steps * (interval === 'weekly' ? 7 : 14)));
    return date.toISOString().slice(0, 10);
  }
  const index = (month - 1) + steps;
  const targetYear = year + Math.floor(index / 12);
  const targetMonth = ((index % 12) + 12) % 12;
  const last = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  const date = new Date(Date.UTC(targetYear, targetMonth, Math.min(day, last)));
  return date.toISOString().slice(0, 10);
}

/**
 * Eşit böl: `amountMinor` → `count` taksit. Taban = kuruşa aşağı bölüm; KURUŞ FARKI SON TAKSİTTE
 * (taslak / K6). Vade ilk vadeden aralıkla; tür hepsinde `method`.
 */
export function splitEqual(amountMinor, count, firstDue, { interval = 'monthly', method = 'promissory' } = {}) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  const total = Math.max(0, Math.floor(Number(amountMinor) || 0));
  if (!n || !firstDue) return [];
  const base = Math.floor(total / n);
  return Array.from({ length: n }, (_, index) => ({
    no: index + 1,
    dueOn: addInterval(firstDue, interval, index),
    amountMinor: index === n - 1 ? total - base * (n - 1) : base,
    method,
  }));
}

/** Taksit numaralarını 1..n yeniden dizer (satır silinince / eklenince). */
export function renumber(installments = []) {
  return installments.map((row, index) => ({ ...row, no: index + 1 }));
}

/**
 * Toplam denetimi: sözleşme tutarı (Σ satır toplamı) = peşinat + Σ taksit. `diffMinor` = sözleşme − ödeme;
 * sıfır değilse "Onayla" kapalıdır. Peşin modda tek ödeme satırı taksit sayılır.
 */
export function totalsCheck({ lines = [], payment = {} } = {}) {
  const contractMinor = linesSummary(lines).totalMinor;
  const downPaymentMinor = payment.mode === 'installment' && payment.downPayment
    ? Math.max(0, Number(payment.downPayment.amountMinor) || 0) : 0;
  const installmentsMinor = (payment.installments || [])
    .reduce((sum, row) => sum + (Number.isInteger(row.amountMinor) ? row.amountMinor : 0), 0);
  const paidMinor = downPaymentMinor + installmentsMinor;
  return { contractMinor, downPaymentMinor, installmentsMinor, paidMinor, diffMinor: contractMinor - paidMinor,
    ok: contractMinor > 0 && contractMinor === paidMinor };
}

/** Önerilen eğitim yılı: başlangıç ayı ve sonrası → "Y-(Y+1)", öncesi → "(Y-1)-Y". */
export function suggestAcademicYear(todayIso, startMonth = 9) {
  const [year, month] = String(todayIso).split('-').map(Number);
  const start = month >= (Number(startMonth) || 9) ? year : year - 1;
  return `${start}-${start + 1}`;
}

/** Seçenek listesi: önerilen yıl, bir önceki ve bir sonraki. */
export function academicYearOptions(suggested) {
  const start = Number(String(suggested).slice(0, 4));
  return [start - 1, start, start + 1].map((year) => `${year}-${year + 1}`);
}

// ------------------------------------------------------------------ taraf

/** Varsayılan ödeme sorumlusu: sunucunun önerisi → ödeme yetkili birincil → ödeme yetkili → ilk veli. */
export function defaultPayerSlot(partyResponse = {}) {
  const guardians = partyResponse?.party?.guardians || partyResponse?.guardians || [];
  const suggested = partyResponse?.defaultPayerSlot ?? partyResponse?.party?.defaultPayerSlot;
  if (suggested && guardians.some((item) => item.slot === suggested)) return suggested;
  const pick = guardians.find((item) => item.canPay && (item.primary || item.isPrimary))
    || guardians.find((item) => item.canPay) || guardians.find((item) => item.primary || item.isPrimary)
    || [...guardians].sort((a, b) => (a.slot || 0) - (b.slot || 0))[0];
  return pick ? pick.slot : null;
}

const STUDENT_REQUIRED = [['fullName', 'Ad soyad'], ['nationalId', 'TC kimlik no (ya da yabancı kimlik no)']];
const PAYER_REQUIRED = [['fullName', 'Ad soyad'], ['nationalId', 'TC kimlik no (ya da yabancı kimlik no)'],
  ['homeAddress', 'Ev adresi'], ['phone', 'Cep telefonu']];

/**
 * Basım için ZORUNLU eksikler (taslak `MissingField`): sunucunun listesi esastır; seçilen ödeme sorumlusu
 * sunucunun hesapladığından farklıysa onun eksikleri maskeli taraf görünümünden de çıkarılır.
 */
export function missingFor(partyResponse = {}, payerSlot = null) {
  const party = partyResponse?.party || {};
  const server = (partyResponse?.missingFields || []).filter((item) => item.scope === 'student'
    || payerSlot === null || item.slot === payerSlot);
  const out = [...server];
  const has = (scope, slot, field) => out.some((item) => item.scope === scope && item.field === field
    && (scope === 'student' || item.slot === slot));
  const student = party.student || {};
  const studentValue = { fullName: student.fullName, nationalId: student.nationalIdMasked || student.foreignIdMasked };
  for (const [field, label] of STUDENT_REQUIRED) {
    if (!studentValue[field] && !has('student', null, field)) {
      out.push({ scope: 'student', slot: null, field, label, blocks: ['registration', 'meb', 'promissory'] });
    }
  }
  const guardian = (party.guardians || []).find((item) => item.slot === payerSlot);
  if (payerSlot !== null && guardian) {
    const value = { fullName: guardian.fullName, nationalId: guardian.nationalIdMasked || guardian.foreignIdMasked,
      homeAddress: guardian.homeAddress?.line || (typeof guardian.homeAddress === 'string' ? guardian.homeAddress : ''),
      phone: guardian.phoneMasked || guardian.phone };
    for (const [field, label] of PAYER_REQUIRED) {
      if (!value[field] && !has('guardian', payerSlot, field)) {
        out.push({ scope: 'guardian', slot: payerSlot, field, label, blocks: ['registration', 'meb', 'promissory'] });
      }
    }
  } else if (payerSlot === null && !(party.guardians || []).length) {
    out.push({ scope: 'guardian', slot: null, field: 'guardian', label: 'Ödeme sorumlusu veli', blocks: ['promissory'] });
  }
  return out;
}

/** Eksik alan → kısa metin: "Öğrenci: TC kimlik no" / "Veli 1: Ev adresi". */
export function missingText(item) {
  const owner = item.scope === 'student' ? 'Öğrenci' : (item.slot ? `Veli ${item.slot}` : 'Veli');
  return `${owner}: ${item.label || item.field}`;
}

/** Adres nesnesi → tek satır. */
export function addressText(address) {
  if (!address) return '';
  if (typeof address === 'string') return address;
  return [address.line, [address.district, address.city].filter(Boolean).join(' / ')].filter(Boolean).join(', ');
}

// ------------------------------------------------------------------ taslak

/** Boş hizmet satırı (katalogdan ya da serbest). */
export function lineFromService(service = null) {
  return {
    serviceId: service?.id || null,
    category: service?.category || 'education',
    name: service?.name || '',
    unit: service?.unit || null,
    unitPriceMinor: Number.isInteger(service?.unitPriceMinor) ? service.unitPriceMinor : null,
    quantity: '1',
    scholarshipPercent: '0',
    discountPercent: '0',
    extraDiscountMinor: 0,
    vatPercent: service?.vatPercent ?? null,
  };
}

/** Mevcut plandan ("Sözleşme oluştur", `planId`) tek hizmet satırı: plan başlığı + tutarı. */
export function lineFromPlan(plan = {}) {
  return {
    ...lineFromService(null),
    name: String(plan.title || 'Eğitim ücreti').slice(0, 120),
    unitPriceMinor: Number(plan.listPriceMinor ?? plan.totalMinor) || 0,
    extraDiscountMinor: Number(plan.discount?.amountMinor ?? plan.discountMinor) || 0,
  };
}

/** Plan kalemleri → sözleşme ödeme bölümü (salt okunur gösterim + toplam denetimi). */
export function paymentFromPlan(plan = {}) {
  const charges = (plan.charges || []).filter((row) => row.status !== 'cancelled');
  const down = charges.find((row) => row.kind === 'down_payment');
  const installments = charges.filter((row) => row.kind !== 'down_payment')
    .map((row, index) => ({ no: index + 1, dueOn: row.dueDate, amountMinor: row.amountMinor,
      method: row.plannedMethod || row.method || 'promissory' }));
  return {
    mode: installments.length ? 'installment' : 'cash',
    downPayment: down ? { amountMinor: down.amountMinor, dueOn: down.dueDate, method: down.plannedMethod || 'cash' } : null,
    installments,
  };
}

/** Satır girdisi → taslak satırı (`ContractLineInput`). */
function lineInput(line) {
  const out = {
    category: line.category || 'other',
    name: String(line.name || '').trim().slice(0, 120),
    unitPriceMinor: Number.isInteger(line.unitPriceMinor) ? line.unitPriceMinor : 0,
    quantity: (hundredths(line.quantity ?? 1) ?? 0) / 100,
    scholarshipPercent: (hundredths(line.scholarshipPercent ?? 0) ?? 0) / 100,
    discountPercent: (hundredths(line.discountPercent ?? 0) ?? 0) / 100,
    extraDiscountMinor: Number.isInteger(line.extraDiscountMinor) ? line.extraDiscountMinor : 0,
  };
  if (line.serviceId) out.serviceId = line.serviceId;
  if (line.unit) out.unit = String(line.unit).slice(0, 20);
  if (line.vatPercent !== undefined && line.vatPercent !== null && line.vatPercent !== '') out.vatPercent = Number(line.vatPercent);
  return out;
}

/** "satır satır" metin → madde listesi (boş satırlar atılır). */
export function linesOf(text) {
  return String(text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

/**
 * Sihirbaz durumu → `ContractDraft` (A5 / A6 gövdesi). `planId` varsa ödeme bölümü GÖNDERİLMEZ değil, plandan
 * okunmuş haliyle gider (sunucu planı bağlar; taslak "planId verilirse mevcut plan bağlanır").
 */
export function draftBody(state) {
  const payment = state.payment || {};
  const installment = payment.mode === 'installment';
  const body = {
    academicYear: state.academicYear,
    registeredOn: state.registeredOn,
    contractDate: state.contractDate,
    payerGuardianSlot: state.payerGuardianSlot,
    lines: (state.lines || []).map(lineInput),
    payment: {
      mode: installment ? 'installment' : 'cash',
      downPayment: installment && payment.downPayment && Number(payment.downPayment.amountMinor) > 0
        ? { amountMinor: Number(payment.downPayment.amountMinor), dueOn: payment.downPayment.dueOn,
          method: payment.downPayment.method || 'cash' }
        : null,
      installments: renumber(payment.installments || []).map((row) => ({ no: row.no, dueOn: row.dueOn,
        amountMinor: row.amountMinor, method: row.method || 'cash' })),
    },
  };
  const reasons = state.discountReasons || [];
  if (reasons.length) body.discountReasons = [...reasons];
  if (String(state.discountNote || '').trim()) body.discountNote = String(state.discountNote).trim();
  const special = Array.isArray(state.specialClauses) ? state.specialClauses : linesOf(state.specialClauses);
  if (special.length) body.specialClauses = special;
  const past = (state.pastPayments || []).filter((row) => Number(row.amountMinor) > 0);
  if (past.length && !state.planId) {
    body.pastPayments = past.map((row) => {
      const item = { receivedOn: row.receivedOn, amountMinor: Number(row.amountMinor), method: row.method || 'cash' };
      if (String(row.note || '').trim()) item.note = String(row.note).trim().slice(0, 200);
      return item;
    });
  }
  body.existingSigned = state.existingSignedOn
    ? { number: String(state.existingNumber || '').trim(), signedOn: state.existingSignedDate }
    : null;
  if (state.planId) body.planId = state.planId;
  return body;
}

/**
 * İstemci doğrulaması (sunucu esastır). Dönüş: [{section, field, message}] — boşsa "Onayla" açılır.
 * section: party | lines | payment | past | contract
 */
export function validateDraft(state, { today } = {}) {
  const errors = [];
  const add = (section, field, message) => errors.push({ section, field, message });
  if (!state.payerGuardianSlot) add('party', 'payerGuardianSlot', 'Ödeme sorumlusu veliyi seçin.');
  const lines = state.lines || [];
  if (!lines.length) add('lines', 'lines', 'En az bir hizmet satırı ekleyin.');
  lines.forEach((line, index) => {
    const no = index + 1;
    if (!String(line.name || '').trim()) add('lines', `lines.${index}.name`, `${no}. satır: hizmet adı yazın.`);
    const row = lineTotals(line);
    if (row.errors.includes('unitPriceMinor')) add('lines', `lines.${index}.unitPriceMinor`, `${no}. satır: birim fiyat yazın.`);
    if (row.errors.includes('quantity')) add('lines', `lines.${index}.quantity`, `${no}. satır: miktar 0'dan büyük olmalı (en çok 2 ondalık).`);
    if (row.errors.includes('scholarshipPercent')) add('lines', `lines.${index}.scholarshipPercent`, `${no}. satır: burs %0–100.`);
    if (row.errors.includes('discountPercent')) add('lines', `lines.${index}.discountPercent`, `${no}. satır: indirim %0–100.`);
    if (row.errors.includes('discountExceeds')) add('lines', `lines.${index}.extraDiscountMinor`, `${no}. satır: indirim tutarı satır tutarını aşıyor.`);
  });
  const payment = state.payment || {};
  const installments = payment.installments || [];
  if (!installments.length) {
    add('payment', 'installments', payment.mode === 'installment' ? 'Taksitleri oluşturun ("Eşit böl").'
      : 'Ödeme tarihini girin.');
  }
  installments.forEach((row, index) => {
    if (!row.dueOn) add('payment', `installments.${index}.dueOn`, `${index + 1}. taksit: vade girin.`);
    if (!Number.isInteger(row.amountMinor) || row.amountMinor <= 0) add('payment', `installments.${index}.amountMinor`, `${index + 1}. taksit: tutar girin.`);
  });
  if (payment.mode === 'installment' && payment.downPayment && Number(payment.downPayment.amountMinor) > 0
    && !payment.downPayment.dueOn) add('payment', 'downPayment.dueOn', 'Peşinat tarihini girin.');
  const check = totalsCheck(state);
  if (check.contractMinor > 0 && check.diffMinor !== 0) {
    add('payment', 'totals', 'Peşinat ve taksitlerin toplamı sözleşme tutarına eşit olmalı.');
  }
  if (check.contractMinor <= 0 && lines.length) add('lines', 'total', 'Sözleşme tutarı 0 olamaz.');
  let pastSum = 0;
  (state.pastPayments || []).forEach((row, index) => {
    if (!(Number(row.amountMinor) > 0)) return;
    pastSum += Number(row.amountMinor);
    if (!row.receivedOn) add('past', `pastPayments.${index}.receivedOn`, `${index + 1}. ödeme: tarih girin.`);
    else if (today && row.receivedOn > today) add('past', `pastPayments.${index}.receivedOn`, `${index + 1}. ödeme: tarih gelecekte olamaz.`);
  });
  if (pastSum > check.contractMinor && check.contractMinor > 0) add('past', 'pastPayments', 'Geçmiş ödemeler sözleşme tutarını aşıyor.');
  if (!state.academicYear) add('contract', 'academicYear', 'Eğitim yılını seçin.');
  if (!state.registeredOn) add('contract', 'registeredOn', 'Kayıt tarihini girin.');
  if (!state.contractDate) add('contract', 'contractDate', 'Sözleşme tarihini girin.');
  if (state.existingSignedOn) {
    const number = String(state.existingNumber || '').trim();
    if (!number) add('contract', 'existingSigned.number', 'Eski sözleşme numarasını yazın.');
    else if (number.length > 20) add('contract', 'existingSigned.number', 'Sözleşme numarası en çok 20 karakter.');
    if (!state.existingSignedDate) add('contract', 'existingSigned.signedOn', 'İmza tarihini girin.');
    else if (today && state.existingSignedDate > today) add('contract', 'existingSigned.signedOn', 'İmza tarihi gelecekte olamaz.');
  }
  return errors;
}

/** "Sonraki: BBD1-1000" (ayar okunamadıysa boş). */
export function nextNumberHint(settings = {}) {
  const prefix = settings.numberPrefix ?? DEFAULT_SETTINGS.numberPrefix;
  const next = settings.nextNumber;
  return Number.isInteger(next) ? `Sonraki: ${prefix}${next}` : '';
}

/** `/v1/admin/settings` yanıtından sözleşme ayarları (iç içe `contracts` ya da düz `contracts.x` anahtarları). */
export function contractSettingsOf(settings) {
  const nested = settings && typeof settings.contracts === 'object' && settings.contracts ? settings.contracts : {};
  const flat = (key) => (settings ? settings[`contracts.${key}`] : undefined);
  const pick = (key) => nested[key] ?? flat(key) ?? DEFAULT_SETTINGS[key];
  const kinds = pick('registrationKinds');
  return {
    numberPrefix: pick('numberPrefix'),
    nextNumber: Number.isInteger(pick('nextNumber')) ? pick('nextNumber') : null,
    academicYearStartMonth: Number(pick('academicYearStartMonth')) || 9,
    registrationKinds: Array.isArray(kinds) && kinds.length ? kinds.filter((kind) => kind in DOCUMENT_KINDS)
      : DEFAULT_SETTINGS.registrationKinds,
    defaultCopies: { ...DEFAULT_SETTINGS.defaultCopies, ...(pick('defaultCopies') || {}) },
  };
}

// ------------------------------------------------------------------ hata zarfı

/**
 * Akademi hata zarfı → alan alan Türkçe ileti. Dönüş: {summary, fields: [{section, field, message}], missing}.
 */
export function contractProblem(error) {
  const code = error?.code || '';
  const details = error?.details;
  if (code === 'contract_totals_mismatch') {
    return { summary: 'Sözleşme tutarı ile peşinat + taksitler toplamı eşit değil.',
      fields: [{ section: 'payment', field: 'totals', message: 'Toplamlar eşit değil — taksit tutarlarını düzeltin ya da "Eşit böl".' }],
      missing: [] };
  }
  if (code === 'contract_missing_fields') {
    const missing = Array.isArray(details) ? details : (details?.missingFields || []);
    return { summary: `Belge için eksik bilgi var: ${missing.map(missingText).join(' · ') || 'kimlik bilgileri'}. Öğrenciler panelinde düzenleyin.`,
      fields: [{ section: 'party', field: 'missing', message: 'Eksik bilgiler Öğrenciler panelinde tamamlanmalı.' }], missing };
  }
  if (code === 'contract_number_taken') {
    return { summary: 'Bu sözleşme numarası başka bir sözleşmede kullanılıyor.',
      fields: [{ section: 'contract', field: 'existingSigned.number', message: 'Bu numara kullanılıyor — başka numara yazın.' }],
      missing: [] };
  }
  if (code === 'validation_error' && Array.isArray(details)) {
    const fields = details.map((item) => {
      const field = String(item?.field || '').replace(/^body\./, '');
      const head = field.split('.')[0];
      const section = head === 'lines' ? 'lines' : head === 'payment' ? 'payment' : head === 'pastPayments' ? 'past'
        : head === 'payerGuardianSlot' ? 'party' : 'contract';
      return { section, field, message: item?.message || 'Geçersiz değer.' };
    });
    return { summary: fields.map((item) => item.message).join(' · ') || 'Bilgiler geçersiz.', fields, missing: [] };
  }
  return { summary: '', fields: [], missing: [] };
}

// ------------------------------------------------------------------ sözleşme ayrıntısı

/** İmza çipi metni + tonu. */
export function signatureChip(summary = {}) {
  const state = summary?.signature?.state || 'awaiting';
  return SIGNATURE_STATES[state] || [state, 'dim'];
}

/** Sürümün hazır belge türleri. */
export function readyKinds(revision = {}) {
  return (revision.documents || []).filter((item) => item.ready).map((item) => item.kind);
}

/** "İmzalandı" penceresinin varsayılan türleri: o sürümde üretilmiş imzalanabilir türler. */
export function defaultSignKinds(revision = {}) {
  const ready = readyKinds(revision).filter((kind) => SIGNABLE_KINDS.includes(kind));
  return ready.length ? ready : [...SIGNABLE_KINDS];
}

/**
 * İmza gövdesi (A10). Doğrulama hatası varsa {error}. `uploads` = yüklenmiş tarama kimlikleri (sırayla).
 */
export function signBody({ revisionNo, signedOn, kinds = [], uploads = [], note = '' } = {}, { today } = {}) {
  if (!Number.isInteger(revisionNo) || revisionNo < 1) return { error: 'Sürüm seçin.' };
  if (!signedOn) return { error: 'İmza tarihini girin.' };
  if (today && signedOn > today) return { error: 'İmza tarihi gelecekte olamaz.' };
  const picked = kinds.filter((kind) => SIGNABLE_KINDS.includes(kind));
  if (!picked.length) return { error: 'İmzalanan en az bir belge seçin.' };
  const body = { revisionNo, signedOn, kinds: picked };
  if (uploads.length) body.scanFileIds = [...uploads];
  if (String(note || '').trim()) body.note = String(note).trim().slice(0, 300);
  return { body };
}

/** Tarama dosyası ön denetimi (UX; asıl kapı Akademi'de — `contract_scan_invalid`). */
export const SCAN_TYPES = ['pdf', 'jpg', 'jpeg', 'png'];
export const SCAN_MAX_MB = 10;
export function scanFileProblem(file = {}) {
  const ext = String(file.name || '').split('.').pop().toLowerCase();
  if (!SCAN_TYPES.includes(ext)) return `${file.name || 'Dosya'}: yalnız PDF, JPG ya da PNG.`;
  if (Number(file.size) > SCAN_MAX_MB * 1024 * 1024) return `${file.name}: ${SCAN_MAX_MB} MB'tan büyük.`;
  return '';
}

/**
 * "Velinin gördüğü" (K8): yalnız SON İMZALI sürümün imzalanan türleri + o sürümün taranmış nüshaları.
 * İmzalı sürüm yoksa veli hiçbir şey görmez.
 */
export function guardianView(detail = {}) {
  const signed = [...(detail.revisions || [])].filter((item) => item.signature)
    .sort((a, b) => b.no - a.no)[0];
  if (!signed) return { revisionNo: null, kinds: [], attachments: [], signedOn: null };
  return {
    revisionNo: signed.no,
    signedOn: signed.signature.signedOn,
    source: signed.signature.source,
    kinds: (signed.signature.kinds || []).filter((kind) => SIGNABLE_KINDS.includes(kind)),
    attachments: (detail.attachments || []).filter((item) => item.revisionNo === signed.no),
  };
}

/** Son sürüm ve imzalı mı (madde düzenleme uyarısı: "yeniden imza gerektirir"). */
export function latestRevision(detail = {}) {
  return [...(detail.revisions || [])].sort((a, b) => b.no - a.no)[0] || null;
}
export function needsResignWarning(detail = {}) {
  return Boolean(detail?.signature?.signedRevisionNo);
}

/** Belgeleri hiç üretilmemiş (eksik alan yüzünden) sürüm mü? → "Belgeleri üret" (A9). */
export function needsRender(revision = {}) {
  const documents = revision.documents || [];
  return !revision.signature && documents.length > 0 && !documents.some((item) => item.ready && SIGNABLE_KINDS.includes(item.kind));
}

/** Kimlik aynası sözleşmenin son sürüm görüntüsünden yeni mi? ("Kimlik bilgileri değişti" bandı.) */
export function partyChanged(detail = {}, identityUpdatedAt = null) {
  const latest = latestRevision(detail);
  if (!identityUpdatedAt || !latest?.createdAt) return false;
  return String(identityUpdatedAt) > String(latest.createdAt);
}

// ------------------------------------------------------------------ madde düzenleyici modeli

/** Maddeleri derin kopyalar (düzenleyici kaynağı bozmasın). */
export function cloneClauses(clauses = []) {
  return (clauses || []).map((clause) => ({ title: clause?.title ?? null, items: [...(clause?.items || [])] }));
}

/** Kayıt öncesi: başlık/öğe kırpılır, boş öğe atılır, öğesiz ve başlıksız madde atılır. */
export function normalizeClauses(clauses = []) {
  return cloneClauses(clauses)
    .map((clause) => ({ title: String(clause.title || '').trim() || null,
      items: clause.items.map((item) => String(item || '').trim()).filter(Boolean) }))
    .filter((clause) => clause.title || clause.items.length);
}

/** İki madde listesi aynı mı (normalize edilmiş)? */
export function sameClauses(a, b) {
  return JSON.stringify(normalizeClauses(a)) === JSON.stringify(normalizeClauses(b));
}

function move(list, from, to) {
  if (from < 0 || from >= list.length || to < 0 || to >= list.length || from === to) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** Madde düzenleyicinin işlemleri (değişmez veri: her işlem YENİ liste döndürür). */
export const clauseOps = {
  addClause: (clauses, at = clauses.length) => {
    const next = cloneClauses(clauses);
    next.splice(at, 0, { title: '', items: [''] });
    return next;
  },
  removeClause: (clauses, index) => cloneClauses(clauses).filter((_, position) => position !== index),
  moveClause: (clauses, from, to) => move(cloneClauses(clauses), from, to),
  setTitle: (clauses, index, title) => cloneClauses(clauses).map((clause, position) => (position === index
    ? { ...clause, title } : clause)),
  addItem: (clauses, index, at = null) => cloneClauses(clauses).map((clause, position) => {
    if (position !== index) return clause;
    const items = [...clause.items];
    items.splice(at === null ? items.length : at, 0, '');
    return { ...clause, items };
  }),
  setItem: (clauses, index, item, text) => cloneClauses(clauses).map((clause, position) => (position === index
    ? { ...clause, items: clause.items.map((value, slot) => (slot === item ? text : value)) } : clause)),
  removeItem: (clauses, index, item) => cloneClauses(clauses).map((clause, position) => (position === index
    ? { ...clause, items: clause.items.filter((_, slot) => slot !== item) } : clause)),
  moveItem: (clauses, index, from, to) => cloneClauses(clauses).map((clause, position) => (position === index
    ? { ...clause, items: move(clause.items, from, to) } : clause)),
};

/**
 * Madde düzenleme gövdesi (A8): yalnız DEĞİŞEN türler gider; gerekçe 3–200. Değişiklik yoksa {error}.
 */
export function reviseBody({ reason = '', original = {}, edited = {}, specialOriginal = [], specialEdited = null } = {}) {
  const text = String(reason || '').trim();
  if (text.length < 3) return { error: 'Gerekçe en az 3 karakter olmalı.' };
  if (text.length > 200) return { error: 'Gerekçe en çok 200 karakter.' };
  const clauses = {};
  for (const kind of CLAUSE_KINDS) {
    if (edited[kind] && !sameClauses(original[kind] || [], edited[kind])) clauses[kind] = normalizeClauses(edited[kind]);
  }
  const body = { reason: text };
  if (Object.keys(clauses).length) body.clauses = clauses;
  if (specialEdited !== null) {
    const next = Array.isArray(specialEdited) ? specialEdited.map((item) => String(item).trim()).filter(Boolean) : linesOf(specialEdited);
    if (JSON.stringify(next) !== JSON.stringify(specialOriginal || [])) body.specialClauses = next;
  }
  if (!body.clauses && !body.specialClauses) return { error: 'Değişiklik yok.' };
  return { body };
}

// ------------------------------------------------------------------ anlık

/** Olay bu ekranı ilgilendiriyor mu? (`contract.changed` / `contract.party_changed`) */
export function eventConcerns(event = {}, { studentId = null, contractId = null } = {}) {
  if (!event || !event.type) return false;
  if (event.type === 'contract.party_changed') return !studentId || event.studentId === studentId;
  if (event.type !== 'contract.changed') return false;
  if (contractId && event.contractId === contractId) return true;
  return Boolean(studentId && event.studentId === studentId);
}

/** Liste parmak izi (yoklama ile değişiklik algılama — satır başına istek yok). */
export function listFingerprint(items = []) {
  return (items || []).map((item) => [item.id, item.latestRevisionNo, item.status, item.signature?.state,
    item.signature?.signedRevisionNo, item.totalMinor].join(':')).join('|');
}

/** Basit dize özeti (Idempotency-Key'in gövdeye bağlanması: aynı gövde = aynı anahtar). */
export function hashText(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}
