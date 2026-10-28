// BBD Akademi — Sözleşme ayarlarının SAF mantığı (IBAN doğrulama, taslak, doğrulama, PATCH gövdesi).
//
// İMPORT'SUZ ve DOM'suz: `.mjs` kopyası Node'da sınanır (`tests/test_akademi_sozlesme_belge.py`).
// Ayarlar Akademi'nin kurum ayarlarıdır (`GET/PATCH /v1/admin/settings` → `contracts.*`, JSON merge yaması):
// yalnız DEĞİŞEN anahtar gönderilir; nesne/liste ayarı (banka hesapları, nüshalar, imza etiketleri) bütün olarak.
// Ayrılma kesinti oranı burada YOK — `finance.withdrawalDeductionPercent` kullanılır (Kurum ayarları → Kurum finansı).

export const KIND_KEYS = ['registration', 'meb', 'promissory', 'payment_plan', 'legal_notice'];

export const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim',
  'Kasım', 'Aralık'];

/** Akademi'de anahtar yoksa (eski sürüm) ekranın başlangıç değerleri — taslaktaki varsayılanlar. */
export const SETTING_DEFAULTS = {
  legalName: '',
  mebSchoolName: '',
  courtCity: '',
  bankAccounts: [],
  numberPrefix: 'BBD1-',
  nextNumber: 1000,
  academicYearStartMonth: 9,
  registrationKinds: ['registration', 'meb', 'promissory', 'payment_plan'],
  defaultCopies: { registration: 2, meb: 2, promissory: 1, payment_plan: 1, legal_notice: 1 },
  guardianVisibility: 'signer',
  signatureLabels: { student: 'Öğrenci', guardian: 'Veli', institution: 'Kurum Yetkilisi' },
};

export const VISIBILITY = {
  signer: ['Yalnız ödeme sorumlusu veli', 'İmzalı sözleşmeyi uygulamada yalnız sözleşmeyi imzalayan (ödeme sorumlusu) '
    + 'veli görür.'],
  finance_guardians: ['Çocuğun finansını gören bütün veliler', 'Öğrencinin ödemelerini görebilen her veli imzalı '
    + 'sözleşmeyi de görür (ör. anne ve baba ikisi de).'],
};

const PREFIX = /^[A-Za-z0-9ÇĞİÖŞÜçğıöşü./_-]{0,10}$/;

/** Türkçe büyük harf (i → İ, ı → I). */
export function upperTr(text) {
  return String(text ?? '').toLocaleUpperCase('tr-TR');
}

/** Boşluksuz, büyük harfli IBAN. */
export function normalizeIban(text) {
  return String(text ?? '').replace(/\s+/g, '').toUpperCase();
}

/** "TR330006100519786457841326" → "TR33 0006 1005 1978 6457 8413 26". */
export function formatIban(text) {
  return normalizeIban(text).replace(/(.{4})/g, '$1 ').trim();
}

/** ISO 13616 mod-97 (harf → sayı, ilk dört karakter sona). Uzun sayı parça parça bölünür. */
function mod97(iban) {
  const moved = iban.slice(4) + iban.slice(0, 4);
  let rest = 0;
  for (const char of moved) {
    const code = char >= 'A' && char <= 'Z' ? String(char.charCodeAt(0) - 55) : char;
    for (const digit of code) rest = (rest * 10 + Number(digit)) % 97;
  }
  return rest;
}

/**
 * TR IBAN doğrulaması: "TR" + 24 rakam (26 karakter) ve mod-97 sağlaması = 1.
 * @returns {string|null} hata metni ya da null (geçerli)
 */
export function ibanError(text) {
  const iban = normalizeIban(text);
  if (!iban) return 'IBAN yazın.';
  if (!iban.startsWith('TR')) return 'IBAN "TR" ile başlamalı.';
  if (!/^TR\d+$/.test(iban)) return 'IBAN\'da "TR"den sonra yalnız rakam olur.';
  if (iban.length !== 26) return `IBAN 26 karakter olmalı (TR + 24 rakam); şu an ${iban.length}.`;
  if (mod97(iban) !== 1) return 'IBAN sağlaması tutmuyor — rakamları kontrol edin.';
  return null;
}

/** Sonraki sözleşme numarasının önizlemesi ("BBD1-" + 1000 → "BBD1-1000"). */
export function nextNumberPreview(prefix, next) {
  const number = Math.round(Number(next));
  return `${String(prefix ?? '')}${Number.isFinite(number) && number > 0 ? number : '?'}`;
}

const cloneDefaults = () => JSON.parse(JSON.stringify(SETTING_DEFAULTS));

/** Sunucunun `contracts` görünümü → ekran taslağı (eksik anahtar varsayılanla). */
export function settingsDraft(contracts) {
  const base = cloneDefaults();
  const data = contracts && typeof contracts === 'object' ? contracts : {};
  const out = { ...base };
  for (const key of Object.keys(base)) {
    if (data[key] !== undefined && data[key] !== null) out[key] = JSON.parse(JSON.stringify(data[key]));
  }
  out.defaultCopies = { ...base.defaultCopies, ...(data.defaultCopies || {}) };
  out.signatureLabels = { ...base.signatureLabels, ...(data.signatureLabels || {}) };
  out.bankAccounts = (Array.isArray(out.bankAccounts) ? out.bankAccounts : []).map((account) => ({
    bank: account?.bank || '', holder: account?.holder || '', branch: account?.branch || '',
    iban: formatIban(account?.iban || '') }));
  out.registrationKinds = KIND_KEYS.filter((key) => (out.registrationKinds || []).includes(key));
  return out;
}

const text = (value) => String(value ?? '').trim();
const optional = (value) => text(value) || null;

/** Taslak → Akademi'ye gidecek değerler (kırpılmış, IBAN boşluksuz, il büyük harf). */
export function normalizeSettings(draft) {
  return {
    legalName: optional(draft.legalName),
    mebSchoolName: optional(draft.mebSchoolName),
    courtCity: optional(upperTr(text(draft.courtCity))),
    bankAccounts: (draft.bankAccounts || [])
      .map((account) => ({ bank: text(account.bank), holder: text(account.holder), branch: optional(account.branch),
        iban: normalizeIban(account.iban) }))
      .filter((account) => account.bank || account.holder || account.iban || account.branch),
    numberPrefix: text(draft.numberPrefix),
    nextNumber: Math.round(Number(draft.nextNumber)),
    academicYearStartMonth: Math.round(Number(draft.academicYearStartMonth)),
    registrationKinds: KIND_KEYS.filter((key) => (draft.registrationKinds || []).includes(key)),
    defaultCopies: Object.fromEntries(KIND_KEYS.map((key) => [key,
      Math.min(3, Math.max(1, Math.round(Number(draft.defaultCopies?.[key]) || 1)))])),
    guardianVisibility: draft.guardianVisibility === 'finance_guardians' ? 'finance_guardians' : 'signer',
    signatureLabels: { student: text(draft.signatureLabels?.student), guardian: text(draft.signatureLabels?.guardian),
      institution: text(draft.signatureLabels?.institution) },
  };
}

/**
 * Doğrulama. Dönüş: `{field: mesaj}`; banka satırları `bank.<sıra>.<alan>` anahtarıyla. Boş nesne = geçerli.
 */
export function validateSettings(draft) {
  const errors = {};
  const value = normalizeSettings(draft);
  if (value.legalName && value.legalName.length > 200) errors.legalName = 'En çok 200 karakter.';
  if (value.mebSchoolName && value.mebSchoolName.length > 200) errors.mebSchoolName = 'En çok 200 karakter.';
  if (value.courtCity && value.courtCity.length > 40) errors.courtCity = 'En çok 40 karakter.';
  if (!PREFIX.test(value.numberPrefix)) {
    errors.numberPrefix = 'En çok 10 karakter; harf, rakam, "-", "/", "." ya da "_".';
  }
  if (!Number.isInteger(value.nextNumber) || value.nextNumber < 1 || value.nextNumber > 999999999) {
    errors.nextNumber = 'Pozitif bir tam sayı yazın (ör. 1000).';
  }
  if (!(value.academicYearStartMonth >= 1 && value.academicYearStartMonth <= 12)) {
    errors.academicYearStartMonth = 'Bir ay seçin.';
  }
  if (!value.registrationKinds.length) errors.registrationKinds = 'Kayıt setinde en az bir belge olmalı.';
  for (const [key, label] of [['student', 'Öğrenci'], ['guardian', 'Veli'], ['institution', 'Kurum']]) {
    const item = value.signatureLabels[key];
    if (!item) errors[`signature.${key}`] = `${label} imza etiketi boş olamaz.`;
    else if (item.length > 40) errors[`signature.${key}`] = 'En çok 40 karakter.';
  }
  value.bankAccounts.forEach((account, index) => {
    if (!account.bank) errors[`bank.${index}.bank`] = 'Banka adı yazın.';
    if (!account.holder) errors[`bank.${index}.holder`] = 'Hesap sahibi yazın.';
    const iban = ibanError(account.iban);
    if (iban) errors[`bank.${index}.iban`] = iban;
  });
  const seen = new Set();
  value.bankAccounts.forEach((account, index) => {
    if (account.iban && seen.has(account.iban)) errors[`bank.${index}.iban`] = 'Bu IBAN listede zaten var.';
    seen.add(account.iban);
  });
  return errors;
}

/**
 * PATCH gövdesi: yalnız değişen `contracts.*` anahtarları (`{contracts: {...}}`); değişiklik yoksa null.
 * `original` sunucunun `contracts` görünümüdür (taslağa çevrilmemiş).
 */
export function settingsPatch(original, draft) {
  const before = normalizeSettings(settingsDraft(original));
  const after = normalizeSettings(draft);
  const changed = {};
  for (const key of Object.keys(after)) {
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) changed[key] = after[key];
  }
  return Object.keys(changed).length ? { contracts: changed } : null;
}

/** Kaç ayar değişti (kaydet şeridi). */
export function dirtyCount(original, draft) {
  const patch = settingsPatch(original, draft);
  return patch ? Object.keys(patch.contracts).length : 0;
}
